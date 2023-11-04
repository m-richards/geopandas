import pytest
import geopandas
from importlib import resources
from pathlib import Path


@pytest.fixture(autouse=True)
def add_geopandas(doctest_namespace):
    doctest_namespace["geopandas"] = geopandas


# Datasets used in our tests


@pytest.fixture(scope="session")
def naturalearth_lowres() -> Path:
    ref = (
        resources.files("geopandas.datasets.naturalearth_lowres")
        / "naturalearth_lowres.shp"
    )
    with resources.as_file(ref) as path:
        yield path


@pytest.fixture(scope="session")
def naturalearth_cities() -> Path:
    ref = (
        resources.files("geopandas.datasets.naturalearth_cities")
        / "naturalearth_cities.shp"
    )
    with resources.as_file(ref) as path:
        yield path


@pytest.fixture(scope="session")
def nybb_zipped() -> str:
    ref = resources.files("geopandas.datasets") / "nybb_16a.zip"
    with resources.as_file(ref) as path:
        yield "zip://" + str(path)

    # should be equivalent to
    # geodatasets = pytest.importorskip("geodatasets")
    # yield Path(geodatasets.get_path("nybb")).parent.parent.parent / "nybb_16a.zip"
