import datetime
import io
import json
import os
import pathlib
import shutil
import tempfile
import warnings
import zoneinfo
from collections import OrderedDict
from packaging.version import Version

import numpy as np
import pandas as pd
from pandas.api.types import is_datetime64_any_dtype

from shapely.geometry import Point, Polygon, box, mapping

import geopandas
from geopandas import GeoDataFrame, GeoSeries, points_from_xy, read_file
from geopandas._compat import HAS_PYPROJ, PANDAS_GE_30
from geopandas.io.file import _EXTENSION_TO_DRIVER, _detect_driver

import pytest
from geopandas.testing import assert_geodataframe_equal, assert_geoseries_equal
from geopandas.tests.util import PACKAGE_DIR, validate_boro_df
from pandas.testing import assert_frame_equal, assert_series_equal

try:
    import pyogrio

    # those version checks have to be defined here instead of imported from
    # geopandas.io.file (those are only initialized lazily on first usage)
    PYOGRIO_GE_090 = Version(Version(pyogrio.__version__).base_version) >= Version(
        "0.9.0"
    )
except ImportError:
    pyogrio = False
    PYOGRIO_GE_090 = False


try:
    import fiona

    FIONA_GE_19 = Version(Version(fiona.__version__).base_version) >= Version("1.9.0")
except ImportError:
    fiona = False
    FIONA_GE_19 = False


PYOGRIO_MARK = pytest.mark.skipif(not pyogrio, reason="pyogrio not installed")
FIONA_MARK = pytest.mark.skipif(not fiona, reason="fiona not installed")


_CRS = "epsg:4326"


pytestmark = pytest.mark.filterwarnings("ignore:Value:RuntimeWarning:pyogrio")


@pytest.fixture(
    params=[
        pytest.param("fiona", marks=FIONA_MARK),
        pytest.param("pyogrio", marks=PYOGRIO_MARK),
    ]
)
def engine(request):
    return request.param


def skip_pyogrio_not_supported(engine):
    if engine == "pyogrio":
        pytest.skip("not supported for the pyogrio engine")


@pytest.fixture
def df_nybb(engine, nybb_filename):
    df = read_file(nybb_filename, engine=engine)
    return df


@pytest.fixture
def df_null():
    return read_file(
        os.path.join(PACKAGE_DIR, "geopandas", "tests", "data", "null_geom.geojson")
    )


@pytest.fixture
def file_path():
    return os.path.join(PACKAGE_DIR, "geopandas", "tests", "data", "null_geom.geojson")


@pytest.fixture
def df_points():
    N = 10
    crs = _CRS
    df = GeoDataFrame(
        [
            {"geometry": Point(x, y), "value1": x + y, "value2": x * y}
            for x, y in zip(range(N), range(N))
        ],
        crs=crs,
    )
    return df


# -----------------------------------------------------------------------------
# to_file tests
# -----------------------------------------------------------------------------

driver_ext_pairs = [
    ("ESRI Shapefile", ".shp"),
    ("GeoJSON", ".geojson"),
    ("GPKG", ".gpkg"),
    (None, ".shp"),
    (None, ""),
    (None, ".geojson"),
    (None, ".gpkg"),
]


def assert_correct_driver(file_path, ext, engine):
    # check the expected driver
    expected_driver = "ESRI Shapefile" if ext == "" else _EXTENSION_TO_DRIVER[ext]

    if engine == "fiona":
        with fiona.open(str(file_path)) as fds:
            assert fds.driver == expected_driver
    else:
        # TODO pyogrio doesn't yet provide a way to check the driver of a file
        return


@pytest.mark.parametrize("driver,ext", driver_ext_pairs)
def test_to_file(tmpdir, df_nybb, df_null, driver, ext, engine):
    """Test to_file and from_file"""
    tempfilename = os.path.join(str(tmpdir), "boros." + ext)
    df_nybb.to_file(tempfilename, driver=driver, engine=engine)
    # Read layer back in
    df = GeoDataFrame.from_file(tempfilename, engine=engine)
    assert "geometry" in df
    assert len(df) == 5
    assert np.all(df["BoroName"].values == df_nybb["BoroName"])

    # Write layer with null geometry out to file
    tempfilename = os.path.join(str(tmpdir), "null_geom" + ext)
    df_null.to_file(tempfilename, driver=driver, engine=engine)
    # Read layer back in
    df = GeoDataFrame.from_file(tempfilename, engine=engine)
    assert "geometry" in df
    assert len(df) == 2
    assert np.all(df["Name"].values == df_null["Name"])
    # check the expected driver
    assert_correct_driver(tempfilename, ext, engine)


@pytest.mark.parametrize("driver,ext", driver_ext_pairs)
def test_to_file_pathlib(tmpdir, df_nybb, driver, ext, engine):
    """Test to_file and from_file"""
    temppath = pathlib.Path(os.path.join(str(tmpdir), "boros." + ext))
    df_nybb.to_file(temppath, driver=driver, engine=engine)
    # Read layer back in
    df = GeoDataFrame.from_file(temppath, engine=engine)
    assert "geometry" in df
    assert len(df) == 5
    assert np.all(df["BoroName"].values == df_nybb["BoroName"])
    # check the expected driver
    assert_correct_driver(temppath, ext, engine)


@pytest.mark.parametrize("driver,ext", driver_ext_pairs)
def test_to_file_bool(tmpdir, driver, ext, engine):
    """Test error raise when writing with a boolean column (GH #437)."""
    tempfilename = os.path.join(str(tmpdir), f"temp.{ext}")
    df = GeoDataFrame(
        {
            "col": [True, False, True],
            "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)],
        },
        crs=4326,
    )

    df.to_file(tempfilename, driver=driver, engine=engine)
    result = read_file(tempfilename, engine=engine)
    if ext in (".shp", ""):
        # Shapefile does not support boolean, so is read back as int
        # but since GDAL 3.9 supports boolean fields in SHP
        if engine == "fiona" and fiona.gdal_version.minor < 9:
            df["col"] = df["col"].astype("int64")
        elif engine == "pyogrio" and pyogrio.__gdal_version__ < (3, 9):
            df["col"] = df["col"].astype("int32")
    assert_geodataframe_equal(result, df)
    # check the expected driver
    assert_correct_driver(tempfilename, ext, engine)


TEST_DATE = datetime.datetime(2021, 11, 21, 1, 7, 43, 17500)
# from pandas 2.0, utc equality checks less stringent, forward compat with zoneinfo
utc = datetime.timezone.utc
eastern = zoneinfo.ZoneInfo("America/New_York")
test_date_eastern = TEST_DATE.replace(tzinfo=eastern)
datetime_type_tests = (TEST_DATE, test_date_eastern)


@pytest.mark.filterwarnings(
    "ignore:Non-conformant content for record 1 in column b:RuntimeWarning"
)  # for GPKG, GDAL writes the tz data but warns on reading (see DATETIME_FORMAT option)
@pytest.mark.parametrize(
    "time", datetime_type_tests, ids=("naive_datetime", "datetime_with_timezone")
)
@pytest.mark.parametrize("driver,ext", driver_ext_pairs)
def test_to_file_datetime(tmpdir, driver, ext, time, engine):
    """Test writing a data file with the datetime column type"""
    if ext in (".shp", ""):
        pytest.skip(f"Driver corresponding to ext {ext} doesn't support dt fields")

    tempfilename = os.path.join(str(tmpdir), f"test_datetime{ext}")
    point = Point(0, 0)

    df = GeoDataFrame(
        {"a": [1.0, 2.0], "b": [time, time]}, geometry=[point, point], crs=4326
    )
    df["b"] = df["b"].dt.round(freq="ms")

    df.to_file(tempfilename, driver=driver, engine=engine)
    df_read = read_file(tempfilename, engine=engine)

    assert_geodataframe_equal(df.drop(columns=["b"]), df_read.drop(columns=["b"]))
    # Check datetime column
    expected = df["b"]
    expected = df["b"].dt.as_unit("ms")
    actual = df_read["b"]
    if df["b"].dt.tz is not None:
        # US/Eastern becomes a UTC-5 fixed offset when read from file
        # as GDAL only models offsets, not timezones.
        # Compare fair result in terms of UTC instead
        expected = expected.dt.tz_convert(utc)
        actual = actual.dt.tz_convert(utc)

    assert_series_equal(expected, actual)


dt_exts = ["gpkg", "geojson"]


def write_invalid_date_file(date_str, tmpdir, ext, engine):
    tempfilename = os.path.join(str(tmpdir), f"test_invalid_datetime.{ext}")
    df = GeoDataFrame(
        {
            "date": ["2014-08-26T10:01:23", "2014-08-26T10:01:23", date_str],
            "geometry": [Point(1, 1), Point(1, 1), Point(1, 1)],
        }
    )
    # Schema not required for GeoJSON since not typed, but needed for GPKG
    if ext == "geojson":
        df.to_file(tempfilename, engine=engine)
    else:
        schema = {"geometry": "Point", "properties": {"date": "datetime"}}
        if engine == "pyogrio" and not fiona:
            # (use schema to write the invalid date without pandas datetimes
            pytest.skip("test requires fiona kwarg schema")
        df.to_file(tempfilename, schema=schema, engine="fiona")
    return tempfilename


@pytest.mark.parametrize("ext", dt_exts)
def test_read_file_datetime_invalid(tmpdir, ext, engine):
    # https://github.com/geopandas/geopandas/issues/2502
    date_str = "9999-99-99T00:00:00"  # invalid date handled by GDAL
    tempfilename = write_invalid_date_file(date_str, tmpdir, ext, engine)
    res = read_file(tempfilename, engine=engine)
    if ext == "gpkg":
        assert is_datetime64_any_dtype(res["date"])
        assert pd.isna(res["date"].iloc[-1])
    else:
        assert res["date"].dtype == "str" if PANDAS_GE_30 else object
        assert isinstance(res["date"].iloc[-1], str)


@pytest.mark.parametrize("ext", dt_exts)
def test_read_file_datetime_out_of_bounds_ns(tmpdir, ext, engine):
    # https://github.com/geopandas/geopandas/issues/2502
    date_str = "9999-12-31T00:00:00"  # valid to GDAL, not to [ns] format
    tempfilename = write_invalid_date_file(date_str, tmpdir, ext, engine)
    res = read_file(tempfilename, engine=engine)
    if PANDAS_GE_30:
        assert res["date"].dtype == "datetime64[ms]"
        assert res["date"].iloc[-1] == pd.Timestamp("9999-12-31 00:00:00")
    else:
        # Pandas invalid datetimes are read in as object dtype (strings)
        assert res["date"].dtype == "object"
        assert isinstance(res["date"].iloc[0], str)


def test_read_file_datetime_mixed_offsets(tmpdir):
    # https://github.com/geopandas/geopandas/issues/2478
    tempfilename = os.path.join(str(tmpdir), "test_mixed_datetime.geojson")
    df = GeoDataFrame(
        {
            "date": [
                "2014-08-26 10:01:23.040001+02:00",
                "2019-03-07 17:31:43.118999+01:00",
            ],
            "geometry": [Point(1, 1), Point(1, 1)],
        }
    )
    df.to_file(tempfilename)
    # check mixed tz don't crash GH2478
    res = read_file(tempfilename)
    # Convert mixed timezones to UTC equivalent
    assert is_datetime64_any_dtype(res["date"])
    assert res["date"].dt.tz == utc


@pytest.mark.parametrize("driver,ext", driver_ext_pairs)
def test_to_file_with_point_z(tmpdir, ext, driver, engine):
    """Test that 3D geometries are retained in writes (GH #612)."""

    tempfilename = os.path.join(str(tmpdir), "test_3Dpoint" + ext)
    point3d = Point(0, 0, 500)
    point2d = Point(1, 1)
    df = GeoDataFrame({"a": [1, 2]}, geometry=[point3d, point2d], crs=_CRS)
    df.to_file(tempfilename, driver=driver, engine=engine)
    df_read = GeoDataFrame.from_file(tempfilename, engine=engine)
    assert_geoseries_equal(df.geometry, df_read.geometry)
    # check the expected driver
    assert_correct_driver(tempfilename, ext, engine)


@pytest.mark.parametrize("driver,ext", driver_ext_pairs)
def test_to_file_with_poly_z(tmpdir, ext, driver, engine):
    """Test that 3D geometries are retained in writes (GH #612)."""

    tempfilename = os.path.join(str(tmpdir), "test_3Dpoly" + ext)
    poly3d = Polygon([[0, 0, 5], [0, 1, 5], [1, 1, 5], [1, 0, 5]])
    poly2d = Polygon([[0, 0], [0, 1], [1, 1], [1, 0]])
    df = GeoDataFrame({"a": [1, 2]}, geometry=[poly3d, poly2d], crs=_CRS)
    df.to_file(tempfilename, driver=driver, engine=engine)
    df_read = GeoDataFrame.from_file(tempfilename, engine=engine)
    assert_geoseries_equal(df.geometry, df_read.geometry)
    # check the expected driver
    assert_correct_driver(tempfilename, ext, engine)


def test_to_file_types(tmpdir, df_points, engine):
    """Test various integer type columns (GH#93)"""
    tempfilename = os.path.join(str(tmpdir), "int.shp")
    int_types = [
        np.int8,
        np.int16,
        np.int32,
        np.int64,
        np.intp,
        np.uint8,
        np.uint16,
        np.uint32,
        np.uint64,
    ]
    geometry = df_points.geometry
    data = {
        str(i): np.arange(len(geometry), dtype=dtype)
        for i, dtype in enumerate(int_types)
    }
    df = GeoDataFrame(data, geometry=geometry)
    df.to_file(tempfilename, engine=engine)


@pytest.mark.parametrize("driver,ext", driver_ext_pairs + [("OGR_GMT", ".gmt")])
def test_to_file_int32(tmpdir, df_points, engine, driver, ext):
    tempfilename = os.path.join(str(tmpdir), f"int32.{ext}")
    geometry = df_points.geometry
    df = GeoDataFrame(geometry=geometry)
    df["data"] = pd.array([1, np.nan] * 5, dtype=pd.Int32Dtype())
    df.to_file(tempfilename, driver=driver, engine=engine)
    df_read = GeoDataFrame.from_file(tempfilename, engine=engine)
    # the int column with missing values comes back as float
    expected = df.copy()
    expected["data"] = expected["data"].astype("float64")
    assert_geodataframe_equal(df_read, expected, check_like=True)

    tempfilename2 = os.path.join(str(tmpdir), f"int32_2.{ext}")
    df2 = df.dropna()
    df2.to_file(tempfilename2, driver=driver, engine=engine)
    df2_read = GeoDataFrame.from_file(tempfilename2, engine=engine)
    if engine == "pyogrio":
        assert df2_read["data"].dtype == "int32"
    else:
        # with the fiona engine the 32 bitwidth is not preserved
        assert df2_read["data"].dtype == "int64"


@pytest.mark.parametrize("driver,ext", driver_ext_pairs)
def test_to_file_int64(tmpdir, df_points, engine, driver, ext):
    tempfilename = os.path.join(str(tmpdir), f"int64.{ext}")
    geometry = df_points.geometry
    df = GeoDataFrame(geometry=geometry)
    df["data"] = pd.array([1, np.nan] * 5, dtype=pd.Int64Dtype())
    df.to_file(tempfilename, driver=driver, engine=engine)
    df_read = GeoDataFrame.from_file(tempfilename, engine=engine)
    # the int column with missing values comes back as float
    expected = df.copy()
    expected["data"] = expected["data"].astype("float64")
    assert_geodataframe_equal(df_read, expected, check_like=True)


def test_to_file_empty(tmpdir, engine):
    input_empty_df = GeoDataFrame(columns=["geometry"])
    tempfilename = os.path.join(str(tmpdir), "test.shp")
    with pytest.warns(UserWarning):
        input_empty_df.to_file(tempfilename, engine=engine)


def test_to_file_schema(tmpdir, df_nybb, engine):
    """
    Ensure that the file is written according to the schema
    if it is specified

    """
    tempfilename = os.path.join(str(tmpdir), "test.shp")
    properties = OrderedDict(
        [
            ("Shape_Leng", "float:19.11"),
            ("BoroName", "str:40"),
            ("BoroCode", "int:10"),
            ("Shape_Area", "float:19.11"),
        ]
    )
    schema = {"geometry": "Polygon", "properties": properties}

    if engine == "pyogrio":
        with pytest.raises(ValueError):
            df_nybb.iloc[:2].to_file(tempfilename, schema=schema, engine=engine)
    else:
        # Take the first 2 features to speed things up a bit
        df_nybb.iloc[:2].to_file(tempfilename, schema=schema, engine=engine)

        import fiona

        with fiona.open(tempfilename) as f:
            result_schema = f.schema

        assert result_schema == schema


@pytest.mark.skipif(not HAS_PYPROJ, reason="pyproj not installed")
def test_to_file_crs(tmpdir, engine, nybb_filename):
    """
    Ensure that the file is written according to the crs
    if it is specified
    """
    df = read_file(nybb_filename, engine=engine)
    tempfilename = os.path.join(str(tmpdir), "crs.shp")

    # save correct CRS
    df.to_file(tempfilename, engine=engine)
    result = GeoDataFrame.from_file(tempfilename, engine=engine)
    assert result.crs == df.crs

    if engine == "pyogrio":
        with pytest.raises(ValueError, match="Passing 'crs' is not supported"):
            df.to_file(tempfilename, crs=3857, engine=engine)
        return

    # overwrite CRS
    df.to_file(tempfilename, crs=3857, engine=engine)
    result = GeoDataFrame.from_file(tempfilename, engine=engine)
    assert result.crs == "epsg:3857"

    # specify CRS for gdf without one
    df2 = df.set_crs(None, allow_override=True)
    df2.to_file(tempfilename, crs=2263, engine=engine)
    df = GeoDataFrame.from_file(tempfilename, engine=engine)
    assert df.crs == "epsg:2263"


def test_to_file_column_len(tmpdir, df_points, engine):
    """
    Ensure that a warning about truncation is given when a geodataframe with
    column names longer than 10 characters is saved to shapefile
    """
    tempfilename = os.path.join(str(tmpdir), "test.shp")

    df = df_points.iloc[:1].copy()
    df["0123456789A"] = ["the column name is 11 characters"]

    with pytest.warns(
        UserWarning, match="Column names longer than 10 characters will be truncated"
    ):
        df.to_file(tempfilename, driver="ESRI Shapefile", engine=engine)


def test_to_file_with_duplicate_columns(tmpdir, engine):
    df = GeoDataFrame(data=[[1, 2, 3]], columns=["a", "b", "a"], geometry=[Point(1, 1)])
    tempfilename = os.path.join(str(tmpdir), "duplicate.shp")
    with pytest.raises(
        ValueError, match="GeoDataFrame cannot contain duplicated column names."
    ):
        df.to_file(tempfilename, engine=engine)


@pytest.mark.parametrize("driver,ext", driver_ext_pairs)
def test_append_file(tmpdir, df_nybb, df_null, driver, ext, engine):
    """Test to_file with append mode and from_file"""
    tempfilename = os.path.join(str(tmpdir), "boros" + ext)
    driver = driver if driver else _detect_driver(tempfilename)

    df_nybb.to_file(tempfilename, driver=driver, engine=engine)
    df_nybb.to_file(tempfilename, mode="a", driver=driver, engine=engine)
    # Read layer back in
    df = GeoDataFrame.from_file(tempfilename, engine=engine)
    assert "geometry" in df
    assert len(df) == (5 * 2)
    expected = pd.concat([df_nybb] * 2, ignore_index=True)
    assert_geodataframe_equal(df, expected, check_less_precise=True)

    if engine == "pyogrio":
        # for pyogrio also ensure append=True works
        tempfilename = os.path.join(str(tmpdir), "boros2" + ext)
        df_nybb.to_file(tempfilename, driver=driver, engine=engine)
        df_nybb.to_file(tempfilename, append=True, driver=driver, engine=engine)
        # Read layer back in
        df = GeoDataFrame.from_file(tempfilename, engine=engine)
        assert len(df) == (len(df_nybb) * 2)

    # Write layer with null geometry out to file
    tempfilename = os.path.join(str(tmpdir), "null_geom" + ext)
    df_null.to_file(tempfilename, driver=driver, engine=engine)
    df_null.to_file(tempfilename, mode="a", driver=driver, engine=engine)
    # Read layer back in
    df = GeoDataFrame.from_file(tempfilename, engine=engine)
    assert "geometry" in df
    assert len(df) == (2 * 2)
    expected = pd.concat([df_null] * 2, ignore_index=True)
    assert_geodataframe_equal(df, expected, check_less_precise=True)


def test_mode_unsupported(tmpdir, df_nybb, engine):
    tempfilename = os.path.join(str(tmpdir), "data.shp")
    with pytest.raises(ValueError, match="'mode' should be one of 'w' or 'a'"):
        df_nybb.to_file(tempfilename, mode="r", engine=engine)


@pytest.mark.filterwarnings("ignore:'crs' was not provided:UserWarning:pyogrio")
@pytest.mark.parametrize("driver,ext", driver_ext_pairs)
def test_empty_crs(tmpdir, driver, ext, engine):
    """Test handling of undefined CRS with GPKG driver (GH #1975)."""
    if ext == ".gpkg":
        pytest.xfail("GPKG is read with Undefined geographic SRS.")

    tempfilename = os.path.join(str(tmpdir), "boros" + ext)
    df = GeoDataFrame(
        {
            "a": [1.0, 2.0, 3.0],
            "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)],
        },
    )

    df.to_file(tempfilename, driver=driver, engine=engine)
    result = read_file(tempfilename, engine=engine)

    if ext == ".geojson":
        # geojson by default assumes epsg:4326
        df.geometry.array.crs = "EPSG:4326"

    assert_geodataframe_equal(result, df)


# -----------------------------------------------------------------------------
# read_file tests
# -----------------------------------------------------------------------------


NYBB_CRS = "epsg:2263"


def test_read_file(engine, nybb_filename):
    df = read_file(nybb_filename, engine=engine)
    validate_boro_df(df)
    if HAS_PYPROJ:
        assert df.crs == NYBB_CRS
    expected_columns = ["BoroCode", "BoroName", "Shape_Leng", "Shape_Area"]
    assert (df.columns[:-1] == expected_columns).all()


@pytest.mark.web
@pytest.mark.parametrize(
    "url",
    [
        # geojson url
        "https://raw.githubusercontent.com/geopandas/geopandas/"
        "main/geopandas/tests/data/null_geom.geojson",
        # url to zip file
        "https://raw.githubusercontent.com/geopandas/geopandas/"
        "main/geopandas/tests/data/nybb_16a.zip",
        # url to zipfile without extension
        "https://geonode.goosocean.org/download/480",
        # url to web service
        "https://demo.pygeoapi.io/stable/collections/obs/items",
    ],
)
def test_read_file_url(engine, url):
    gdf = read_file(url, engine=engine)
    assert isinstance(gdf, geopandas.GeoDataFrame)


def test_read_file_local_uri(file_path, engine):
    local_uri = "file://" + file_path
    gdf = read_file(local_uri, engine=engine)
    assert isinstance(gdf, geopandas.GeoDataFrame)


@pytest.mark.skipif(not HAS_PYPROJ, reason="pyproj not installed")
def test_read_file_geojson_string_path(engine):
    if engine == "pyogrio" and not PYOGRIO_GE_090:
        pytest.skip("fixed in pyogrio 0.9.0")
    expected = GeoDataFrame({"val_with_hash": ["row # 0"], "geometry": [Point(0, 1)]})
    features = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"val_with_hash": "row # 0"},
                "geometry": {"type": "Point", "coordinates": [0.0, 1.0]},
            }
        ],
    }
    df_read = read_file(json.dumps(features), engine=engine)
    assert_geodataframe_equal(expected.set_crs("EPSG:4326"), df_read)


def test_read_file_textio(file_path, engine):
    file_text_stream = open(file_path)
    file_stringio = io.StringIO(open(file_path).read())
    gdf_text_stream = read_file(file_text_stream, engine=engine)
    gdf_stringio = read_file(file_stringio, engine=engine)
    assert isinstance(gdf_text_stream, geopandas.GeoDataFrame)
    assert isinstance(gdf_stringio, geopandas.GeoDataFrame)


def test_read_file_bytesio(file_path, engine):
    file_binary_stream = open(file_path, "rb")
    file_bytesio = io.BytesIO(open(file_path, "rb").read())
    gdf_binary_stream = read_file(file_binary_stream, engine=engine)
    gdf_bytesio = read_file(file_bytesio, engine=engine)
    assert isinstance(gdf_binary_stream, geopandas.GeoDataFrame)
    assert isinstance(gdf_bytesio, geopandas.GeoDataFrame)


def test_read_file_raw_stream(file_path, engine):
    file_raw_stream = open(file_path, "rb", buffering=0)
    gdf_raw_stream = read_file(file_raw_stream, engine=engine)
    assert isinstance(gdf_raw_stream, geopandas.GeoDataFrame)


def test_read_file_pathlib(file_path, engine):
    path_object = pathlib.Path(file_path)
    gdf_path_object = read_file(path_object, engine=engine)
    assert isinstance(gdf_path_object, geopandas.GeoDataFrame)


def test_read_file_tempfile(engine):
    temp = tempfile.TemporaryFile()
    temp.write(
        b"""
    {
      "type": "Feature",
      "geometry": {
        "type": "Point",
        "coordinates": [0, 0]
      },
      "properties": {
        "name": "Null Island"
      }
    }
    """
    )
    temp.seek(0)
    gdf_tempfile = geopandas.read_file(temp, engine=engine)
    assert isinstance(gdf_tempfile, geopandas.GeoDataFrame)
    temp.close()


def test_read_binary_file_fsspec(engine, nybb_filename):
    fsspec = pytest.importorskip("fsspec")
    # Remove the zip scheme so fsspec doesn't open as a zipped file,
    # instead we want to read as bytes and let fiona decode it.
    path = nybb_filename[6:]
    with fsspec.open(path, "rb") as f:
        gdf = read_file(f, engine=engine)
        assert isinstance(gdf, geopandas.GeoDataFrame)


def test_read_text_file_fsspec(file_path, engine):
    fsspec = pytest.importorskip("fsspec")
    with fsspec.open(file_path, "r") as f:
        gdf = read_file(f, engine=engine)
        assert isinstance(gdf, geopandas.GeoDataFrame)


@pytest.mark.skipif(not HAS_PYPROJ, reason="pyproj not installed")
def test_read_file_crs_warning_messages(engine):
    """
    Testing warning messages when one of, or both of, the
    file or mask is missing a crs.
    """
    file_with_crs = os.path.join(tempfile.mkdtemp(), "file_with_crs.shp")
    file_without_crs = os.path.join(tempfile.mkdtemp(), "file_without_crs.shp")

    df = pd.DataFrame(
        {
            "Latitude": [0, 1, 2, 3, 4],
            "Longitude": [0, 1, 2, 3, 4],
        }
    )

    gdf_with_crs = GeoDataFrame(
        df, geometry=points_from_xy(df.Longitude, df.Latitude), crs="EPSG:4326"
    )
    gdf_without_crs = GeoDataFrame(
        df, geometry=points_from_xy(df.Longitude, df.Latitude)
    )

    gdf_with_crs.to_file(file_with_crs)
    gdf_without_crs.to_file(file_without_crs)

    with pytest.warns(UserWarning, match="""There is no CRS defined in the mask."""):
        data = read_file(file_with_crs, mask=gdf_without_crs, engine=engine)
        assert_geodataframe_equal(data, gdf_with_crs)

    with pytest.warns(
        UserWarning, match="""There is no CRS defined in the source dataset."""
    ):
        data = read_file(file_without_crs, mask=gdf_with_crs, engine=engine)
        assert_geodataframe_equal(data, gdf_without_crs)

    with pytest.warns(
        UserWarning,
        match="""There is no CRS defined in the source dataset nor mask.""",
    ):
        data = read_file(file_without_crs, mask=gdf_without_crs, engine=engine)
        assert_geodataframe_equal(data, gdf_without_crs)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        data = read_file(file_with_crs, mask=gdf_with_crs, engine=engine)
        assert_geodataframe_equal(data, gdf_with_crs)

    # Check with GeoSeries as a mask
    series_with_crs = GeoSeries(
        points_from_xy(df.Longitude, df.Latitude), crs="EPSG:4326"
    )

    series_without_crs = GeoSeries(points_from_xy(df.Longitude, df.Latitude))

    with pytest.warns(UserWarning, match="""There is no CRS defined in the mask."""):
        data = read_file(file_with_crs, mask=series_without_crs, engine=engine)
        assert_geodataframe_equal(data, gdf_with_crs)

    with pytest.warns(
        UserWarning, match="""There is no CRS defined in the source dataset."""
    ):
        data = read_file(file_without_crs, mask=series_with_crs, engine=engine)
        assert_geodataframe_equal(data, gdf_without_crs)

    with pytest.warns(
        UserWarning,
        match="""There is no CRS defined in the source dataset nor mask.""",
    ):
        data = read_file(file_without_crs, mask=series_without_crs, engine=engine)
        assert_geodataframe_equal(data, gdf_without_crs)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        data = read_file(file_with_crs, mask=series_with_crs, engine=engine)
        assert_geodataframe_equal(data, gdf_with_crs)


def test_infer_zipped_file(engine, nybb_filename):
    # Remove the zip scheme so that the test for a zipped file can
    # check it and add it back.
    path = nybb_filename[6:]
    gdf = read_file(path, engine=engine)
    assert isinstance(gdf, geopandas.GeoDataFrame)

    # Check that it can successfully add a zip scheme to a path that already has a
    # scheme
    gdf = read_file("file+file://" + path, engine=engine)
    assert isinstance(gdf, geopandas.GeoDataFrame)

    # Check that it can add a zip scheme for a path that includes a subpath
    # within the archive.
    gdf = read_file(path + "!nybb.shp", engine=engine)
    assert isinstance(gdf, geopandas.GeoDataFrame)


def test_allow_legacy_gdal_path(engine, nybb_filename):
    # Construct a GDAL-style zip path.
    path = "/vsizip/" + nybb_filename[6:]
    gdf = read_file(path, engine=engine)
    assert isinstance(gdf, geopandas.GeoDataFrame)


@pytest.mark.skipif(not PYOGRIO_GE_090, reason="bug fixed in pyogrio 0.9.0")
def test_read_file_with_hash_in_path(engine, nybb_filename, tmp_path):
    folder_with_hash = tmp_path / "path with # present"
    folder_with_hash.mkdir(exist_ok=True, parents=True)
    read_path = folder_with_hash / "nybb.zip"
    shutil.copy(nybb_filename[6:], read_path)
    gdf = read_file(read_path, engine=engine)
    assert isinstance(gdf, geopandas.GeoDataFrame)


def test_read_file_bbox_tuple(df_nybb, engine, nybb_filename):
    bbox = (
        1031051.7879884212,
        224272.49231459625,
        1047224.3104931959,
        244317.30894023244,
    )
    filtered_df = read_file(nybb_filename, bbox=bbox, engine=engine)
    expected = df_nybb[df_nybb["BoroName"].isin(["Bronx", "Queens"])]
    assert_geodataframe_equal(filtered_df, expected.reset_index(drop=True))


def test_read_file_bbox_polygon(df_nybb, engine, nybb_filename):
    bbox = box(
        1031051.7879884212, 224272.49231459625, 1047224.3104931959, 244317.30894023244
    )
    filtered_df = read_file(nybb_filename, bbox=bbox, engine=engine)
    expected = df_nybb[df_nybb["BoroName"].isin(["Bronx", "Queens"])]
    assert_geodataframe_equal(filtered_df, expected.reset_index(drop=True))


def test_read_file_filtered__rows(df_nybb, engine, nybb_filename):
    filtered_df = read_file(nybb_filename, rows=1, engine=engine)
    assert_geodataframe_equal(filtered_df, df_nybb.iloc[[0], :])


def test_read_file_filtered__rows_slice(df_nybb, engine, nybb_filename):
    filtered_df = read_file(nybb_filename, rows=slice(1, 3), engine=engine)
    assert_geodataframe_equal(filtered_df, df_nybb.iloc[1:3, :].reset_index(drop=True))


@pytest.mark.filterwarnings(
    "ignore:Layer does not support OLC_FASTFEATURECOUNT:RuntimeWarning"
)  # for the slice with -1
def test_read_file_filtered__rows_bbox(df_nybb, engine, nybb_filename):
    bbox = (
        1031051.7879884212,
        224272.49231459625,
        1047224.3104931959,
        244317.30894023244,
    )
    if engine == "fiona":
        # combination bbox and rows (rows slice applied after bbox filtering!)
        filtered_df = read_file(
            nybb_filename, bbox=bbox, rows=slice(4, None), engine=engine
        )
        assert filtered_df.empty

    if engine == "pyogrio":
        # TODO: support negative rows in pyogrio
        with pytest.raises(
            ValueError,
            match="'skip_features' must be between 0 and 1|Negative slice start",
        ):
            filtered_df = read_file(
                nybb_filename, bbox=bbox, rows=slice(-1, None), engine=engine
            )
    else:
        filtered_df = read_file(
            nybb_filename, bbox=bbox, rows=slice(-1, None), engine=engine
        )
        filtered_df["BoroCode"] = filtered_df["BoroCode"].astype("int64")
        assert_geodataframe_equal(
            filtered_df, df_nybb.iloc[4:, :].reset_index(drop=True)
        )


def test_read_file_filtered_rows_invalid(engine, nybb_filename):
    with pytest.raises(TypeError):
        read_file(nybb_filename, rows="not_a_slice", engine=engine)


def test_read_file__ignore_geometry(engine, naturalearth_lowres):
    pdf = geopandas.read_file(
        naturalearth_lowres,
        ignore_geometry=True,
        engine=engine,
    )
    assert "geometry" not in pdf.columns
    assert isinstance(pdf, pd.DataFrame) and not isinstance(pdf, geopandas.GeoDataFrame)


@pytest.mark.filterwarnings(
    "ignore:The 'include_fields' and 'ignore_fields' keywords:DeprecationWarning"
)
def test_read_file__ignore_fields(engine, naturalearth_lowres):
    gdf = geopandas.read_file(
        naturalearth_lowres,
        ignore_fields=["pop_est", "continent", "iso_a3", "gdp_md_est"],
        engine=engine,
    )
    assert gdf.columns.tolist() == ["name", "geometry"]


@pytest.mark.filterwarnings(
    "ignore:The 'include_fields' and 'ignore_fields' keywords:DeprecationWarning"
)
def test_read_file__ignore_all_fields(engine, naturalearth_lowres):
    gdf = geopandas.read_file(
        naturalearth_lowres,
        ignore_fields=["pop_est", "continent", "name", "iso_a3", "gdp_md_est"],
        engine=engine,
    )
    assert gdf.columns.tolist() == ["geometry"]


def test_read_file_missing_geometry(tmpdir, engine):
    filename = str(tmpdir / "test.csv")

    expected = pd.DataFrame(
        {"col1": np.array([1, 2, 3], dtype="int64"), "col2": ["a", "b", "c"]}
    )
    expected.to_csv(filename, index=False)

    df = geopandas.read_file(filename, engine=engine)
    # both engines read integers as strings; force back to original type
    df["col1"] = df["col1"].astype("int64")

    assert isinstance(df, pd.DataFrame)
    assert not isinstance(df, geopandas.GeoDataFrame)

    assert_frame_equal(df, expected)


def test_read_file_None_attribute(tmp_path, engine):
    # Test added in context of https://github.com/geopandas/geopandas/issues/2901
    test_path = tmp_path / "test.gpkg"
    gdf = GeoDataFrame(
        {"a": [None, None]}, geometry=[Point(1, 2), Point(3, 4)], crs=4326
    )

    gdf.to_file(test_path, engine=engine)
    read_gdf = read_file(test_path, engine=engine)
    assert_geodataframe_equal(gdf, read_gdf)


def test_read_csv_dtype(tmpdir, df_nybb):
    filename = str(tmpdir / "test.csv")

    df_nybb.to_csv(filename, index=False)
    pdf = pd.read_csv(filename, dtype={"geometry": "geometry"})

    assert pdf.geometry.dtype == "geometry"


def test_read_file__where_filter(engine, naturalearth_lowres):
    if FIONA_GE_19 or engine == "pyogrio":
        gdf = geopandas.read_file(
            naturalearth_lowres,
            where="continent='Africa'",
            engine=engine,
        )
        assert gdf.continent.unique().tolist() == ["Africa"]
    else:
        with pytest.raises(NotImplementedError):
            geopandas.read_file(
                naturalearth_lowres,
                where="continent='Africa'",
                engine="fiona",
            )


def test_read_file__columns(engine, naturalearth_lowres):
    if engine == "fiona" and not FIONA_GE_19:
        pytest.skip("columns requires fiona 1.9+")

    gdf = geopandas.read_file(
        naturalearth_lowres, columns=["name", "pop_est"], engine=engine
    )
    assert gdf.columns.tolist() == ["name", "pop_est", "geometry"]


def test_read_file__columns_empty(engine, naturalearth_lowres):
    if engine == "fiona" and not FIONA_GE_19:
        pytest.skip("columns requires fiona 1.9+")

    gdf = geopandas.read_file(naturalearth_lowres, columns=[], engine=engine)
    assert gdf.columns.tolist() == ["geometry"]


@pytest.mark.skipif(FIONA_GE_19 or not fiona, reason="test for fiona < 1.9")
def test_read_file__columns_old_fiona(naturalearth_lowres):
    with pytest.raises(NotImplementedError):
        geopandas.read_file(
            naturalearth_lowres, columns=["name", "pop_est"], engine="fiona"
        )


@pytest.mark.filterwarnings(
    "ignore:The 'include_fields' and 'ignore_fields' keywords:DeprecationWarning"
)
def test_read_file__include_fields(engine, naturalearth_lowres):
    if engine == "fiona" and not FIONA_GE_19:
        pytest.skip("columns requires fiona 1.9+")

    gdf = geopandas.read_file(
        naturalearth_lowres, include_fields=["name", "pop_est"], engine=engine
    )
    assert gdf.columns.tolist() == ["name", "pop_est", "geometry"]


@pytest.mark.skipif(not FIONA_GE_19, reason="columns requires fiona 1.9+")
def test_read_file__columns_conflicting_keywords(engine, naturalearth_lowres):
    path = naturalearth_lowres

    with pytest.raises(ValueError, match="Cannot specify both"):
        geopandas.read_file(
            path, include_fields=["name"], ignore_fields=["pop_est"], engine=engine
        )

    with pytest.raises(ValueError, match="Cannot specify both"):
        geopandas.read_file(
            path, columns=["name"], include_fields=["pop_est"], engine=engine
        )

    with pytest.raises(ValueError, match="Cannot specify both"):
        geopandas.read_file(
            path, columns=["name"], ignore_fields=["pop_est"], engine=engine
        )


@pytest.mark.skipif(not HAS_PYPROJ, reason="pyproj not installed")
@pytest.mark.parametrize("file_like", [False, True])
def test_read_file_bbox_gdf(df_nybb, engine, nybb_filename, file_like):
    full_df_shape = df_nybb.shape
    bbox = geopandas.GeoDataFrame(
        geometry=[
            box(
                1031051.7879884212,
                224272.49231459625,
                1047224.3104931959,
                244317.30894023244,
            )
        ],
        crs=NYBB_CRS,
    )
    infile = (
        open(nybb_filename.replace("zip://", ""), "rb") if file_like else nybb_filename
    )
    filtered_df = read_file(infile, bbox=bbox, engine=engine)
    filtered_df_shape = filtered_df.shape
    assert full_df_shape != filtered_df_shape
    assert filtered_df_shape == (2, 5)


@pytest.mark.skipif(not HAS_PYPROJ, reason="pyproj not installed")
@pytest.mark.parametrize("file_like", [False, True])
def test_read_file_mask_gdf(df_nybb, engine, nybb_filename, file_like):
    full_df_shape = df_nybb.shape
    mask = geopandas.GeoDataFrame(
        geometry=[
            box(
                1031051.7879884212,
                224272.49231459625,
                1047224.3104931959,
                244317.30894023244,
            )
        ],
        crs=NYBB_CRS,
    )
    infile = (
        open(nybb_filename.replace("zip://", ""), "rb") if file_like else nybb_filename
    )
    filtered_df = read_file(infile, mask=mask, engine=engine)
    filtered_df_shape = filtered_df.shape
    assert full_df_shape != filtered_df_shape
    assert filtered_df_shape == (2, 5)


def test_read_file_mask_polygon(df_nybb, engine, nybb_filename):
    full_df_shape = df_nybb.shape
    mask = box(
        1031051.7879884212, 224272.49231459625, 1047224.3104931959, 244317.30894023244
    )
    filtered_df = read_file(nybb_filename, mask=mask, engine=engine)
    filtered_df_shape = filtered_df.shape
    assert full_df_shape != filtered_df_shape
    assert filtered_df_shape == (2, 5)


def test_read_file_mask_geojson(df_nybb, nybb_filename, engine):
    full_df_shape = df_nybb.shape
    mask = mapping(
        box(
            1031051.7879884212,
            224272.49231459625,
            1047224.3104931959,
            244317.30894023244,
        )
    )
    filtered_df = read_file(nybb_filename, mask=mask, engine=engine)
    filtered_df_shape = filtered_df.shape
    assert full_df_shape != filtered_df_shape
    assert filtered_df_shape == (2, 5)


@pytest.mark.skipif(not HAS_PYPROJ, reason="pyproj not installed")
def test_read_file_bbox_gdf_mismatched_crs(df_nybb, engine, nybb_filename):
    full_df_shape = df_nybb.shape
    bbox = geopandas.GeoDataFrame(
        geometry=[
            box(
                1031051.7879884212,
                224272.49231459625,
                1047224.3104931959,
                244317.30894023244,
            )
        ],
        crs=NYBB_CRS,
    )
    bbox.to_crs(epsg=4326, inplace=True)
    filtered_df = read_file(nybb_filename, bbox=bbox, engine=engine)
    filtered_df_shape = filtered_df.shape
    assert full_df_shape != filtered_df_shape
    assert filtered_df_shape == (2, 5)


@pytest.mark.skipif(not HAS_PYPROJ, reason="pyproj not installed")
def test_read_file_mask_gdf_mismatched_crs(df_nybb, engine, nybb_filename):
    full_df_shape = df_nybb.shape
    mask = geopandas.GeoDataFrame(
        geometry=[
            box(
                1031051.7879884212,
                224272.49231459625,
                1047224.3104931959,
                244317.30894023244,
            )
        ],
        crs=NYBB_CRS,
    )
    mask.to_crs(epsg=4326, inplace=True)
    filtered_df = read_file(nybb_filename, mask=mask.geometry, engine=engine)
    filtered_df_shape = filtered_df.shape
    assert full_df_shape != filtered_df_shape
    assert filtered_df_shape == (2, 5)


@pytest.mark.skipif(not HAS_PYPROJ, reason="pyproj not installed")
def test_read_file_multi_layer_with_layer_arg_no_warning(tmp_path, engine):
    # While reading a file with multiple layers, if the layer is properly
    # specified, a "Specify layer" warning should not be emitted
    data1 = {"geometry": [Point(1, 2), Point(2, 1)]}
    gdf = geopandas.GeoDataFrame(data1, crs="EPSG:4326")
    file_path = tmp_path / "out.gpkg"
    gdf.to_file(file_path, layer="layer1", engine=engine)
    gdf.to_file(file_path, layer="layer2", engine=engine)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        read_file(file_path, bbox=gdf, layer="layer1", engine=engine)
        read_file(file_path, mask=gdf, layer="layer1", engine=engine)
        specify_layer_warnings = [
            warning
            for warning in captured
            if warning.category is UserWarning
            and "specify layer parameter" in str(warning.message).lower()
        ]
        assert len(specify_layer_warnings) == 0, (
            "'Specify layer parameter' warning was raised, but the layer was specified."
        )


def test_read_file_bbox_mask_not_allowed(engine, nybb_filename):
    bbox = (
        1031051.7879884212,
        224272.49231459625,
        1047224.3104931959,
        244317.30894023244,
    )

    mask = box(*bbox)

    with pytest.raises(ValueError, match="mask and bbox can not be set together"):
        read_file(nybb_filename, bbox=bbox, mask=mask)


@pytest.mark.filterwarnings(
    "ignore:Layer 'b'test_empty'' does not have any features:UserWarning"
)
def test_read_file_empty_shapefile(tmpdir, engine):
    if engine == "pyogrio" and not fiona:
        pytest.skip("test requires fiona to work")
    from geopandas.io.file import fiona_env

    # create empty shapefile
    meta = {
        "crs": {},
        "crs_wkt": "",
        "driver": "ESRI Shapefile",
        "schema": {
            "geometry": "Point",
            "properties": OrderedDict([("A", "int:9"), ("Z", "float:24.15")]),
        },
    }

    fname = str(tmpdir.join("test_empty.shp"))

    with fiona_env():
        with fiona.open(fname, "w", **meta) as _:
            pass

    empty = read_file(fname, engine=engine)
    assert isinstance(empty, geopandas.GeoDataFrame)
    assert all(empty.columns == ["A", "Z", "geometry"])


class FileNumber:
    def __init__(self, tmpdir, base, ext):
        self.tmpdir = str(tmpdir)
        self.base = base
        self.ext = ext
        self.fileno = 0

    def __repr__(self):
        filename = f"{self.base}{self.fileno:02d}.{self.ext}"
        return os.path.join(self.tmpdir, filename)

    def __next__(self):
        self.fileno += 1
        return repr(self)


@pytest.mark.parametrize(
    "driver,ext", [("ESRI Shapefile", "shp"), ("GeoJSON", "geojson")]
)
def test_write_index_to_file(tmpdir, df_points, driver, ext, engine):
    fngen = FileNumber(tmpdir, "check", ext)

    def do_checks(df, index_is_used):
        # check combinations of index=None|True|False on GeoDataFrame/GeoSeries
        other_cols = list(df.columns)
        other_cols.remove("geometry")

        if driver == "ESRI Shapefile":
            # ESRI Shapefile will add FID if no other columns exist
            driver_col = ["FID"]
        else:
            driver_col = []

        if index_is_used:
            index_cols = list(df.index.names)
        else:
            index_cols = [None] * len(df.index.names)

        # replicate pandas' default index names for regular and MultiIndex
        if index_cols == [None]:
            index_cols = ["index"]
        elif len(index_cols) > 1 and not all(index_cols):
            for level, index_col in enumerate(index_cols):
                if index_col is None:
                    index_cols[level] = "level_" + str(level)

        # check GeoDataFrame with default index=None to autodetect
        tempfilename = next(fngen)
        df.to_file(tempfilename, driver=driver, index=None, engine=engine)
        df_check = read_file(tempfilename, engine=engine)
        if len(other_cols) == 0:
            expected_cols = driver_col[:]
        else:
            expected_cols = []
        if index_is_used:
            expected_cols += index_cols
        expected_cols += other_cols + ["geometry"]
        assert list(df_check.columns) == expected_cols

        # similar check on GeoSeries with index=None
        tempfilename = next(fngen)
        df.geometry.to_file(tempfilename, driver=driver, index=None, engine=engine)
        df_check = read_file(tempfilename, engine=engine)
        if index_is_used:
            expected_cols = index_cols + ["geometry"]
        else:
            expected_cols = driver_col + ["geometry"]
        assert list(df_check.columns) == expected_cols

        # check GeoDataFrame with index=True
        tempfilename = next(fngen)
        df.to_file(tempfilename, driver=driver, index=True, engine=engine)
        df_check = read_file(tempfilename, engine=engine)
        assert list(df_check.columns) == index_cols + other_cols + ["geometry"]

        # similar check on GeoSeries with index=True
        tempfilename = next(fngen)
        df.geometry.to_file(tempfilename, driver=driver, index=True, engine=engine)
        df_check = read_file(tempfilename, engine=engine)
        assert list(df_check.columns) == index_cols + ["geometry"]

        # check GeoDataFrame with index=False
        tempfilename = next(fngen)
        df.to_file(tempfilename, driver=driver, index=False, engine=engine)
        df_check = read_file(tempfilename, engine=engine)
        if len(other_cols) == 0:
            expected_cols = driver_col + ["geometry"]
        else:
            expected_cols = other_cols + ["geometry"]
        assert list(df_check.columns) == expected_cols

        # similar check on GeoSeries with index=False
        tempfilename = next(fngen)
        df.geometry.to_file(tempfilename, driver=driver, index=False, engine=engine)
        df_check = read_file(tempfilename, engine=engine)
        assert list(df_check.columns) == driver_col + ["geometry"]

    #
    # Checks where index is not used/saved
    #

    # index is a default RangeIndex
    df_p = df_points.copy()
    df = GeoDataFrame(df_p["value1"], geometry=df_p.geometry)
    do_checks(df, index_is_used=False)

    # index is a RangeIndex, starting from 1
    df.index += 1
    do_checks(df, index_is_used=False)

    # index is a Int64Index regular sequence from 1
    df_p.index = list(range(1, len(df) + 1))
    df = GeoDataFrame(df_p["value1"], geometry=df_p.geometry)
    do_checks(df, index_is_used=False)

    # index was a default RangeIndex, but delete one row to make an Int64Index
    df_p = df_points.copy()
    df = GeoDataFrame(df_p["value1"], geometry=df_p.geometry).drop(5, axis=0)
    do_checks(df, index_is_used=False)

    # no other columns (except geometry)
    df = GeoDataFrame(geometry=df_p.geometry)
    do_checks(df, index_is_used=False)

    #
    # Checks where index is used/saved
    #

    # named index
    df_p = df_points.copy()
    df = GeoDataFrame(df_p["value1"], geometry=df_p.geometry)
    df.index.name = "foo_index"
    do_checks(df, index_is_used=True)

    # named index, same as pandas' default name after .reset_index(drop=False)
    df.index.name = "index"
    do_checks(df, index_is_used=True)

    # named MultiIndex
    df_p = df_points.copy()
    df_p["value3"] = df_p["value2"] - df_p["value1"]
    df_p.set_index(["value1", "value2"], inplace=True)
    df = GeoDataFrame(df_p, geometry=df_p.geometry)
    do_checks(df, index_is_used=True)

    # partially unnamed MultiIndex
    df.index.names = ["first", None]
    do_checks(df, index_is_used=True)

    # unnamed MultiIndex
    df.index.names = [None, None]
    do_checks(df, index_is_used=True)

    # unnamed Float64Index
    df_p = df_points.copy()
    df = GeoDataFrame(df_p["value1"], geometry=df_p.geometry)
    df.index = df_p.index.astype(float) / 10
    do_checks(df, index_is_used=True)

    # named Float64Index
    df.index.name = "centile"
    do_checks(df, index_is_used=True)

    # index as string
    df_p = df_points.copy()
    df = GeoDataFrame(df_p["value1"], geometry=df_p.geometry)
    df.index = pd.to_timedelta(range(len(df)), unit="days")
    # TODO: TimedeltaIndex is an invalid field type
    df.index = df.index.astype(str)
    do_checks(df, index_is_used=True)

    # unnamed DatetimeIndex
    df_p = df_points.copy()
    df = GeoDataFrame(df_p["value1"], geometry=df_p.geometry)
    df.index = pd.to_timedelta(range(len(df)), unit="days") + pd.to_datetime(
        ["1999-12-27"] * len(df)
    )
    if driver == "ESRI Shapefile":
        # Shapefile driver does not support datetime fields
        df.index = df.index.astype(str)
    do_checks(df, index_is_used=True)

    # named DatetimeIndex
    df.index.name = "datetime"
    do_checks(df, index_is_used=True)


def test_to_file__undetermined_driver(tmp_path, df_nybb):
    shpdir = tmp_path / "boros.invalid"
    df_nybb.to_file(shpdir)
    assert shpdir.is_dir()
    assert list(shpdir.glob("*.shp"))


@pytest.mark.parametrize(
    "test_file", [(pathlib.Path("~/test_file.geojson")), "~/test_file.geojson"]
)
def test_write_read_file(test_file, engine):
    gdf = geopandas.GeoDataFrame(geometry=[box(0, 0, 10, 10)], crs=_CRS)
    gdf.to_file(test_file, driver="GeoJSON")
    df_json = geopandas.read_file(test_file, engine=engine)
    assert_geodataframe_equal(gdf, df_json, check_crs=True)
    os.remove(os.path.expanduser(test_file))


@pytest.mark.skipif(fiona is False, reason="Fiona not available")
@pytest.mark.skipif(FIONA_GE_19, reason="Fiona >= 1.9 supports metadata")
def test_to_file_metadata_unsupported_fiona_version(tmp_path, df_points):
    metadata = {"title": "test"}
    tmp_file = tmp_path / "test.gpkg"
    match = "'metadata' keyword is only supported for Fiona >= 1.9"
    with pytest.raises(NotImplementedError, match=match):
        df_points.to_file(tmp_file, driver="GPKG", engine="fiona", metadata=metadata)


@pytest.mark.skipif(not FIONA_GE_19, reason="only Fiona >= 1.9 supports metadata")
def test_to_file_metadata_supported_fiona_version(tmp_path, df_points):
    metadata = {"title": "test"}
    tmp_file = tmp_path / "test.gpkg"

    df_points.to_file(tmp_file, driver="GPKG", engine="fiona", metadata=metadata)

    # Check that metadata is written to the file
    with fiona.open(tmp_file) as src:
        tags = src.tags()
        assert tags == metadata


@pytest.mark.skipif(pyogrio is False, reason="Pyogrio not available")
def test_to_file_metadata_pyogrio(tmp_path, df_points):
    metadata = {"title": "test"}
    tmp_file = tmp_path / "test.gpkg"

    df_points.to_file(tmp_file, driver="GPKG", engine="pyogrio", metadata=metadata)

    # Check that metadata is written to the file
    info = pyogrio.read_info(tmp_file)
    layer_metadata = info["layer_metadata"]
    assert layer_metadata == metadata


@pytest.mark.parametrize(
    "driver, ext", [("ESRI Shapefile", ".shp"), ("GeoJSON", ".geojson")]
)
def test_to_file_metadata_unsupported_driver(driver, ext, tmpdir, df_points, engine):
    metadata = {"title": "Test"}
    tempfilename = os.path.join(str(tmpdir), "test" + ext)
    with pytest.raises(
        NotImplementedError, match="'metadata' keyword is only supported for"
    ):
        df_points.to_file(tempfilename, driver=driver, metadata=metadata)


def test_multiple_geom_cols_error(tmpdir, df_nybb):
    df_nybb["geom2"] = df_nybb.geometry
    with pytest.raises(ValueError, match="GeoDataFrame contains multiple geometry"):
        df_nybb.to_file(os.path.join(str(tmpdir), "boros.gpkg"))


@PYOGRIO_MARK
@FIONA_MARK
def test_option_io_engine(nybb_filename):
    try:
        geopandas.options.io_engine = "pyogrio"

        # disallowing to read a Shapefile with fiona should ensure we are
        # actually reading with pyogrio
        import fiona

        orig = fiona.supported_drivers["ESRI Shapefile"]
        fiona.supported_drivers["ESRI Shapefile"] = "w"

        _ = geopandas.read_file(nybb_filename)
    finally:
        fiona.supported_drivers["ESRI Shapefile"] = orig
        geopandas.options.io_engine = None


@pytest.mark.skipif(pyogrio, reason="test for pyogrio not installed")
def test_error_engine_unavailable_pyogrio(tmp_path, df_points, file_path):
    with pytest.raises(ImportError, match="the 'read_file' function requires"):
        geopandas.read_file(file_path, engine="pyogrio")

    with pytest.raises(ImportError, match="the 'to_file' method requires"):
        df_points.to_file(tmp_path / "test.gpkg", engine="pyogrio")


@pytest.mark.skipif(fiona, reason="test for fiona not installed")
def test_error_engine_unavailable_fiona(tmp_path, df_points, file_path):
    with pytest.raises(ImportError, match="the 'read_file' function requires"):
        geopandas.read_file(file_path, engine="fiona")

    with pytest.raises(ImportError, match="the 'to_file' method requires"):
        df_points.to_file(tmp_path / "test.gpkg", engine="fiona")


def test_error_monkeypatch_engine_unavailable_pyogrio(
    monkeypatch, tmp_path, df_points, file_path
) -> None:
    # monkeypatch to make pyogrio unimportable
    monkeypatch.setattr(geopandas.io.file, "_import_pyogrio", lambda: None)
    monkeypatch.setattr(geopandas.io.file, "pyogrio", None)
    monkeypatch.setattr(
        geopandas.io.file, "pyogrio_import_error", "No module named 'pyogrio'"
    )

    with pytest.raises(ImportError, match="No module named 'pyogrio'"):
        geopandas.read_file(file_path, engine="pyogrio")

    with pytest.raises(ImportError, match="No module named 'pyogrio'"):
        df_points.to_file(tmp_path / "test.gpkg", engine="pyogrio")


def test_error_monkeypatch_engine_unavailable_fiona(
    monkeypatch, tmp_path, df_points, file_path
) -> None:
    # monkeypatch to make fiona unimportable
    monkeypatch.setattr(geopandas.io.file, "_import_fiona", lambda: None)
    monkeypatch.setattr(geopandas.io.file, "fiona", None)
    monkeypatch.setattr(
        geopandas.io.file, "fiona_import_error", "No module named 'fiona'"
    )

    with pytest.raises(ImportError, match="No module named 'fiona'"):
        geopandas.read_file(file_path, engine="fiona")

    with pytest.raises(ImportError, match="No module named 'fiona'"):
        df_points.to_file(tmp_path / "test.gpkg", engine="fiona")


@PYOGRIO_MARK
def test_list_layers(df_points, tmpdir):
    tempfilename = os.path.join(str(tmpdir), "dataset.gpkg")
    df_points.to_file(tempfilename, layer="original")
    df_points.set_geometry(df_points.buffer(1)).to_file(tempfilename, layer="buffered")
    df_points.set_geometry(df_points.buffer(2).boundary).to_file(
        tempfilename, layer="boundary"
    )
    pyogrio.write_dataframe(
        df_points[["value1", "value2"]], tempfilename, layer="non-spatial"
    )
    layers = geopandas.list_layers(tempfilename)
    expected = pd.DataFrame(
        {
            "name": ["original", "buffered", "boundary", "non-spatial"],
            "geometry_type": ["Point", "Polygon", "LineString", None],
        }
    )
    assert_frame_equal(layers, expected)
