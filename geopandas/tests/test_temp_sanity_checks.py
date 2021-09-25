# TODO suspect we might not need to overload astype after this
import pytest
import pandas as pd
from geopandas import GeoDataFrame
from shapely.geometry import Point

# gpd.geom

N = 10


@pytest.fixture
def df():
    return GeoDataFrame(
        [
            {"geometry": Point(x, y), "value1": x + y, "value2": x * y}
            for x, y in zip(range(N), range(N))
        ],
    )


def test_astype_geometry(df):

    df["geometry"] = df["geometry"].astype(str)
    assert type(df) == pd.DataFrame
    print(type(df))
    df.buffer(10)


def test_current_geometry_reassignment_behaviour(df):
    # Question: Should this return a DataFrame or not, do we allow bad choices or not
    # / is there a use for something that is likely not what most users would intend
    df["geometry"] = 3
    assert type(df) == pd.DataFrame
    print(type(df))
    df.buffer(10)


def test_current_rename_behaviour(df):
    # think this is linked to the above test (except that we could
    # automatically propagate the geometry column
    res = df.rename(columns={"geometry": "foo"})
    assert type(res) == pd.DataFrame


def test_astype_geometry2(df):
    # copied from existing astype tests in test_pandas_methods
    res = df.astype(str)
    assert type(res) is pd.DataFrame
    # df.buffer(10)


def test_value_counts(df):
    # copied from existing astype tests in test_pandas_methods
    res = df.groupby("geometry", sort=False).count()
    print(res)
    assert type(res) is pd.DataFrame

    res = df.groupby("value2", sort=False).count()
    print(res)
    assert type(res) is pd.DataFrame
    # df.buffer(10)


def test_multi_geo_col(df):
    df = pd.DataFrame(df)
    df["value1"] = df["geometry"]
    df.columns = ["geometry", "geometry", "value2"]
    with pytest.raises(
        ValueError,
        match="GeoDataFrame does not support multiple columns using"
        " the geometry column "
        "name 'geometry'",
    ):
        GeoDataFrame(df)

    # df.columns = ["foo", "foo", "value2"]
    # gdf = GeoDataFrame(df, geometry="foo")
