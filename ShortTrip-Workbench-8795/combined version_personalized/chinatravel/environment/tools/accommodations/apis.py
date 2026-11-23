import pandas as pd
from chinatravel.environment.lodging_prices import estimate_ranges
from chinatravel.environment.planning_access import scoped_frame
from pandas import DataFrame
from typing import Callable
from geopy.distance import geodesic
import os

import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from poi.apis import Poi
from chinatravel.environment.language import city_lookup, normalize_lang, relative_database_path


class Accommodations:

    def __init__(self, base_path: str = None, en_version=False, lang=None, *, cities=None, poi=None):
        self.lang = normalize_lang(lang, en_version=en_version)
        if base_path is None:
            base_path = relative_database_path(self.lang, "accommodations")
        curdir = os.path.dirname(os.path.realpath(__file__))
        city_labels = city_lookup(self.lang, cities)
        city_list = tuple(city_labels)
        data_path_list = [
            os.path.join(curdir, f"{base_path}/{city}/accommodations.csv")
            for city in city_list
        ]
        self.data = {}
        for i, city in enumerate(city_list):
            self.data[city] = scoped_frame(pd.read_csv(data_path_list[i]), 'accommodation', city, self.lang)
        self.key_type_tuple_list = {}
        for city in city_list:
            self.key_type_tuple_list[city] = []
            for key in self.data[city].keys():
                self.key_type_tuple_list[city].append(
                    (key, type(self.data[city].iloc[0][key]))
                )
        city_cn_list = tuple(city_labels.values())

        for i, city in enumerate(city_list):
            self.data[city_cn_list[i]] = self.data.pop(city)
            self.key_type_tuple_list[city_cn_list[i]] = self.key_type_tuple_list.pop(
                city
            )

        self.poi = poi if poi is not None else Poi(lang=self.lang, cities=cities)
        self._price_ranges = {}
        if self.lang == "zh" and "xian" in city_list:
            provenance_path = os.path.join(curdir, base_path, "xian", "price_provenance.csv")
            self._price_ranges = estimate_ranges(provenance_path)

    def price_metadata(self, city, hotel):
        """Attach the source price status and, for estimates, the reference range."""
        status = hotel.get("price_status")
        if not isinstance(status, str):
            return {}
        metadata = {"price_status": status}
        if city == "西安" and status.startswith("estimated"):
            metadata.update(self._price_ranges.get(hotel["name"], {}))
        return metadata

    def keys(self, city):
        return self.key_type_tuple_list[city]

    def select(self, city, key, func: Callable) -> DataFrame:
        if key not in self.data[city].keys():
            return "Key not found."
        bool_list = [False if pd.isna(x) else bool(func(x)) for x in self.data[city][key]]
        return self.data[city][bool_list]

    def nearby(self, city, point: str, topk: int = None, dist: float = 5) -> DataFrame:
        lat_lon = self.poi.search(city, point)
        if isinstance(lat_lon, str):
            return lat_lon
        lat, lon = lat_lon
        distance = [
            geodesic((lat, lon), (x, y)).km
            for x, y in zip(self.data[city]["lat"], self.data[city]["lon"])
        ]
        tmp = self.data[city].copy()
        tmp["distance"] = distance
        if dist is not None:
            tmp = tmp[tmp["distance"] < dist]
        tmp = tmp.sort_values(by=["distance"])
        if topk is not None:
            return tmp.head(topk)
        return tmp


if __name__ == "__main__":

    AccommodationsAPI = Accommodations()
    print(AccommodationsAPI.keys("南京"))

    # def query_key(key):
    #     print("query key {}".format(key))
    #     print(AccommodationsAPI.get_info(key))

    # for key in ["Price", "numBed", "hotelName"]:
    #     query_key(key)

    # def query_nearby(lat=32.040158, lon=118.823291):

    #     print("query nearby ({}, {}): ".format(lat, lon))
    #     print(AccommodationsAPI.nearby(lat=lat, lon=lon, topk=None, dist=2))

    # query_nearby()

    # print(AccommodationsAPI.select("numBed", 2))

    # print(AccommodationsAPI.data['featureHotelType'].unique())
