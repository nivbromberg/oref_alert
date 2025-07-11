#!/usr/bin/env python3
"""Generate the metadata files."""

import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import requests
import yaml

sys.path.insert(0, str((Path(__file__).parent / "..").resolve()))

from custom_components.oref_alert.metadata import ALL_AREAS_ALIASES

RELATIVE_OUTPUT_DIRECTORY = "custom_components/oref_alert/metadata/"
AREAS_OUTPUT = "areas.py"
AREAS_AND_GROUPS_OUTPUT = "areas_and_groups.py"
CITY_ALL_AREAS_OUTPUT = "city_all_areas.py"
AREA_TO_MIGUN_TIME_OUTPUT = "area_to_migun_time.py"
DISTRICT_TO_AREAS_OUTPUT = "district_to_areas.py"
AREA_TO_DISTRICT_OUTPUT = "area_to_district.py"
AREA_TO_POLYGON_OUTPUT = "area_to_polygon.json"
AREA_INFO_OUTPUT = "area_info.py"
SERVICES_YAML = "custom_components/oref_alert/services.yaml"
TEST_AREAS_FIXTURE = "tests/fixtures/GetCitiesMix.json"
CITIES_MIX_URL = "https://alerts-history.oref.org.il/Shared/Ajax/GetCitiesMix.aspx"
DISTRICTS_URL = "https://alerts-history.oref.org.il/Shared/Ajax/GetDistricts.aspx"
TZEVAADOM_VERSIONS_URL = "https://api.tzevaadom.co.il/lists-versions"
TZEVAADOM_CITIES_URL = "https://www.tzevaadom.co.il/static/cities.json?v="
TZEVAADOM_POLYGONS_URL = "https://www.tzevaadom.co.il/static/polygons.json?v="
CITY_ALL_AREAS_SUFFIX = " - כל האזורים"
# "Hadera all areas" is listed with this typo:
CITY_ALL_AREAS_SUFFIX_TYPO = " כל - האזורים"
DISTRICT_PREFIX = "מחוז "

MISSING_CITIES = {
    "ברחבי הארץ": {"lat": 31.7781, "lon": 35.2164},
    "כל הארץ": {"lat": 31.7781, "lon": 35.2164},
    "אל-ח'וואלד מערב": {"lat": 32.771, "lon": 35.1363},
    "אשדוד -יא,יב,טו,יז,מרינה,סיטי": {"lat": 31.7836, "lon": 34.6332},  # noqa: RUF001
    "כמאנה": {"lat": 32.9085, "lon": 35.3358},
    "נאות חובב": {"lat": 31.1336, "lon": 34.7899},
}


class OrefMetadata:
    """Copy Oref metadata."""

    def __init__(self) -> None:
        """Initialize the object."""
        self.proxy = None
        self._read_args()
        self._root_directory = Path(__file__).parent.parent
        self._output_directory = self._root_directory / RELATIVE_OUTPUT_DIRECTORY
        self._cities_mix: list[Any] = self._fetch_url_json(CITIES_MIX_URL)
        self._backend_areas: list[str] = self._get_areas()
        self._areas_no_group = list(
            filter(
                lambda area: not area.endswith(CITY_ALL_AREAS_SUFFIX),
                self._backend_areas,
            )
        )
        self._city_to_areas: dict[str, list[str]] = self._city_to_areas_map()
        self._area_to_migun_time: dict[str, int] = self._area_to_migun_time_map()
        self._districts = self._get_districts()
        self._district_to_areas = self._district_to_areas_map()
        self._area_to_district = self._area_to_district_map()
        self._areas_and_groups = (
            self._areas_no_group
            + list(self._city_to_areas.keys())
            + list(self._district_to_areas.keys())
        )
        self._areas_and_groups.sort()
        assert len(self._areas_and_groups) == len(set(self._areas_and_groups))
        self._tzeva_cities, self._tzeva_polygons = self._get_tzevaadom_data()
        self._area_to_polygon = self._get_area_to_polygon()
        self._area_info = self._get_area_info()

    def _read_args(self) -> None:
        """Read program arguments."""
        parser = argparse.ArgumentParser()
        parser.add_argument("--proxy")
        parser.parse_args(namespace=self)

    def _fetch_url_json(self, url: str) -> Any:
        """Fetch URL and return JSON reply."""
        return requests.get(
            url,
            proxies={"https": self.proxy} if self.proxy else None,
            timeout=15,
        ).json()

    def _get_areas(self) -> list[str]:
        """Return the list of areas."""
        areas = list(
            {
                area["label_he"].replace(
                    CITY_ALL_AREAS_SUFFIX_TYPO, CITY_ALL_AREAS_SUFFIX
                )
                for area in self._cities_mix
            }
        )
        areas.sort()
        return areas

    def _get_cities_with_all_areas(self) -> list[str]:
        """Return the list of cities with 'all area'."""
        cities = [
            area.replace(CITY_ALL_AREAS_SUFFIX, "")
            for area in filter(
                lambda area: area.endswith(CITY_ALL_AREAS_SUFFIX), self._backend_areas
            )
        ]
        cities.sort()
        return cities

    def _city_to_areas_map(self) -> dict[str, list[str]]:
        """Build the map between cities and their sub areas."""
        city_to_areas = {}
        for city in self._get_cities_with_all_areas():
            city_areas = []
            for area in self._areas_no_group:
                if area.startswith(city):
                    assert area in self._areas_no_group
                    city_areas.append(area)
            city_areas = list(set(city_areas))
            city_areas.sort()
            city_to_areas[city + CITY_ALL_AREAS_SUFFIX] = city_areas
        return city_to_areas

    def _area_to_migun_time_map(self) -> dict[str, int]:
        """Build a mpa between a city and the migun time."""
        migun_time = {
            area["label_he"].replace(
                CITY_ALL_AREAS_SUFFIX_TYPO, CITY_ALL_AREAS_SUFFIX
            ): int(area["migun_time"])
            for area in self._cities_mix
        }
        return {area: migun_time[area] for area in sorted(migun_time.keys())}

    def _get_districts(self) -> list:
        """Return the list of districts."""
        districts = self._fetch_url_json(DISTRICTS_URL)
        return list(
            filter(
                lambda area: area["value"] is not None
                and area["label"] not in ["כל הארץ", "ברחבי הארץ"],
                districts,
            )
        )

    def _district_to_areas_map(self) -> dict[str, list[str]]:
        """Build the map between districts and their areas."""
        district_to_areas = {}
        district_names = list({district["areaname"] for district in self._districts})
        district_names.sort()
        for district in district_names:
            district_areas = []
            for area in self._districts:
                if (
                    area["areaname"] == district
                    and area["label_he"] in self._areas_no_group
                ):
                    assert area["label_he"] not in self._city_to_areas
                    district_areas.append(area["label_he"])
            district_areas = list(set(district_areas))
            district_areas.sort()
            district_to_areas[DISTRICT_PREFIX + district] = district_areas
        return district_to_areas

    def _area_to_district_map(self) -> dict[str, str]:
        """Build the map between areas and their districts."""
        return dict(
            sorted(
                {area["label_he"]: area["areaname"] for area in self._districts}.items()
            )
        )

    def _get_tzevaadom_data(self) -> tuple[Any, Any]:
        """Get tzevaadom metadata content."""
        versions = self._fetch_url_json(TZEVAADOM_VERSIONS_URL)
        return (
            self._fetch_url_json(f"{TZEVAADOM_CITIES_URL}{versions['cities']}")[
                "cities"
            ],
            self._fetch_url_json(f"{TZEVAADOM_POLYGONS_URL}{versions['polygons']}"),
        )

    def _get_area_to_polygon(self) -> dict[str, list[list[float]]]:
        """Get area polygons from tzevaadom."""
        city_list = list(
            set(self._tzeva_cities.keys()).intersection(set(self._areas_no_group))
        )
        city_list.sort()
        return {
            city: self._tzeva_polygons[str(self._tzeva_cities[city]["id"])]
            for city in city_list
            if str(self._tzeva_cities[city]["id"]) in self._tzeva_polygons
        }

    def _get_area_info(self) -> dict[str, list[list[float]]]:
        """Get area additional information from tzevaadom."""
        areas = {}

        overlap = set(MISSING_CITIES.keys()).intersection(
            set(self._tzeva_cities.keys())
        )
        assert not len(overlap), f"Missing cities include Tzeva Adom areas: {overlap}"

        unknown = set(MISSING_CITIES.keys()) - set(self._areas_no_group)
        assert not len(unknown), f"Missing cities include unknown areas: {unknown}"

        uncovered = (
            set(self._areas_no_group)
            - set(self._tzeva_cities.keys())
            - set(MISSING_CITIES.keys())
        )
        assert not len(uncovered), f"Areas with no info: {uncovered}"

        for area in self._areas_no_group:
            if area in self._tzeva_cities:
                areas[area] = {
                    "lat": self._tzeva_cities[area]["lat"],
                    "lon": self._tzeva_cities[area]["lng"],
                }
            else:
                areas[area] = MISSING_CITIES[area]
        return areas

    def generate(self) -> None:
        """Generate the output files."""
        for file_name, variable_name, variable_data in (
            (AREAS_AND_GROUPS_OUTPUT, "AREAS_AND_GROUPS", self._areas_and_groups),
            (CITY_ALL_AREAS_OUTPUT, "CITY_ALL_AREAS", self._city_to_areas),
            (AREA_TO_MIGUN_TIME_OUTPUT, "AREA_TO_MIGUN_TIME", self._area_to_migun_time),
            (DISTRICT_TO_AREAS_OUTPUT, "DISTRICT_AREAS", self._district_to_areas),
            (AREA_TO_DISTRICT_OUTPUT, "AREA_TO_DISTRICT", self._area_to_district),
            (
                AREAS_OUTPUT,
                "AREAS",
                str(self._areas_no_group).replace("[", "{").replace("]", "}"),
            ),
            (AREA_INFO_OUTPUT, "AREA_INFO", self._area_info),
        ):
            with (self._output_directory / file_name).open(
                "w",
                encoding="utf-8",
            ) as output:
                output.write('"""GENERATED FILE. DO NOT EDIT MANUALLY."""\n\n')
                output.write(f"{variable_name} = {variable_data}")

        with (self._root_directory / SERVICES_YAML).open(
            encoding="utf-8",
        ) as services_yaml:
            services = yaml.load(services_yaml, Loader=yaml.SafeLoader)
        services["add_sensor"]["fields"]["areas"]["selector"]["select"]["options"] = [
            area for area in self._areas_and_groups if area not in ALL_AREAS_ALIASES
        ]
        services["synthetic_alert"]["fields"]["area"]["selector"]["select"][
            "options"
        ] = self._areas_no_group
        with (self._root_directory / SERVICES_YAML).open(
            "w",
            encoding="utf-8",
        ) as output:
            yaml.dump(services, output, sort_keys=False, indent=2, allow_unicode=True)

        with (
            zipfile.ZipFile(
                f"{self._output_directory / AREA_TO_POLYGON_OUTPUT}.zip",
            ) as zip_file,
            zip_file.open(AREA_TO_POLYGON_OUTPUT) as json_file,
        ):
            previous_area_to_polygon = json.load(json_file)

        if self._area_to_polygon != previous_area_to_polygon:
            with zipfile.ZipFile(
                f"{self._output_directory / AREA_TO_POLYGON_OUTPUT}.zip",
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as zip_file:
                zip_file.writestr(
                    AREA_TO_POLYGON_OUTPUT,
                    json.dumps(self._area_to_polygon, ensure_ascii=False),
                )

        with (self._root_directory / TEST_AREAS_FIXTURE).open(
            "w", encoding="utf-8"
        ) as fixture:
            json.dump(
                [
                    {"label_he": label_he}
                    for label_he in sorted(
                        {area["label_he"] for area in self._cities_mix}
                    )
                ],
                fixture,
                ensure_ascii=False,
            )

        subprocess.run(["ruff", "format", self._output_directory], check=False)  # noqa: S603, S607


if __name__ == "__main__":
    OrefMetadata().generate()
