from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Any, Set, Optional
from urllib.parse import urlencode
import json
from bs4 import BeautifulSoup


import requests
from tenacity import retry, stop_after_attempt, wait_exponential


@dataclass
class Location:
    lat: float
    lon: float


@dataclass
class City:
    id: int
    uuid: str
    name: str
    country: str
    language: str
    location: Location
    slug: str
    search_volume: int
    transportation_category: List[str]


@dataclass
class ScrapedCity:
    name: str
    slug: str
    letter: str


@dataclass
class Station:
    id: str
    name: str
    legacy_id: int
    importance_order: int
    is_train: bool


@dataclass
class SearchResult:
    id: str
    name: str
    country: str
    district: Optional[str]
    location: Location
    score: float
    legacy_id: int
    stations: List[Station]
    has_train_station: bool
    is_flixbus_city: bool
    timezone_offset_seconds: int

    @property
    def relevance(self) -> float:
        """
        Calculate relevance score based on multiple factors
        
        Returns:
            Float between 0 and 1, where 1 is most relevant
        """
        base_weight = self.score / 100  # Normalize score to 0-1 range
        
        # Add bonus for being a FlixBus city
        flixbus_bonus = 0.2 if self.is_flixbus_city else 0
        
        # Add bonus for having multiple stations
        station_bonus = min(len(self.stations) * 0.1, 0.3)
        
        # Add bonus for having a train station
        train_bonus = 0.1 if self.has_train_station else 0
        
        # Calculate final score (capped at 1.0)
        return min(base_weight + flixbus_bonus + station_bonus + train_bonus, 1.0)


class FlixBusScraper:
    BASE_URL = "https://global.api.flixbus.com"
    WEB_URL = "https://flixbus.com"
    
    def __init__(self):
        self.session = requests.Session()
        self.default_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.flixbus.com",
            "Referer": "https://www.flixbus.com/"
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    def _make_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Make a GET request to the FlixBus API
        
        Args:
            endpoint: API endpoint
            params: Query parameters
            
        Returns:
            JSON response as dictionary
            
        Raises:
            Exception: If request fails or response is invalid
        """
        try:
            url = f"{self.BASE_URL}/{endpoint}"
            response = self.session.get(
                url,
                params=params,
                headers=self.default_headers
            )
            
            response.raise_for_status()
            
            return response.json()
            
        except requests.exceptions.RequestException as e:
            print(f"Request failed: {str(e)}")
            raise
        except json.JSONDecodeError as e:
            print(f"Failed to parse JSON response: {str(e)}")
            raise

    def get_cities(
        self,
        language: str = "en",
        country: str = "",
        limit: int = 6000
    ) -> Dict[str, Any]:
        """
        Get list of cities where FlixBus operates
        
        Args:
            language: Two-letter language code
            country: Two-letter country code
            limit: Maximum number of results
            
        Returns:
            Dictionary containing cities and count
        """
        params = {
            "language": language,
            "country": country,
            "limit": limit
        }
        
        return self._make_request("cms/cities", params)

    def get_reachable_cities(
        self,
        city_id: str,
        language: str = "en",
        country: str = "NL",
        limit: int = 5
    ) -> Dict[str, Any]:
        """
        Get cities reachable from a specific city
        
        Args:
            city_id: UUID of the origin city
            language: Two-letter language code
            country: Two-letter country code
            limit: Maximum number of results
            
        Returns:
            Dictionary containing reachable cities
        """
        params = {
            "language": language,
            "country": country,
            "limit": limit
        }
        
        return self._make_request(f"cms/cities/{city_id}/reachable", params)

    def search_trips(
        self,
        from_city_id: str,
        to_city_id: str,
        departure_date: datetime,
        num_adults: int = 1,
        currency: str = "EUR",
        locale: str = "en",
        include_after_midnight: bool = True,
        disable_distribusion: bool = False,
        disable_global_trips: bool = False
    ) -> Dict[str, Any]:
        """
        Search for available trips between two cities
        
        Args:
            from_city_id: UUID of departure city
            to_city_id: UUID of arrival city
            departure_date: Departure date
            num_adults: Number of adult passengers
            currency: Three-letter currency code
            locale: Two-letter locale code
            include_after_midnight: Include rides after midnight
            disable_distribusion: Disable distribusion trips
            disable_global_trips: Disable global trips
            
        Returns:
            Dictionary containing available trips
        """
        products = {"adult": num_adults}
        
        params = {
            "from_city_id": from_city_id,
            "to_city_id": to_city_id,
            "departure_date": departure_date.strftime("%d.%m.%Y"),
            "products": json.dumps(products),
            "currency": currency,
            "locale": locale,
            "search_by": "cities",
            "include_after_midnight_rides": int(include_after_midnight),
            "disable_distribusion_trips": int(disable_distribusion),
            "disable_global_trips": int(disable_global_trips)
        }
        
        return self._make_request("search/service/v4/search", params)

    def parse_city(self, city_data: Dict[str, Any]) -> City:
        """
        Parse raw city data into City object
        
        Args:
            city_data: Raw city data from API
            
        Returns:
            City object
        """
        location = Location(
            lat=city_data["location"]["lat"],
            lon=city_data["location"]["lon"]
        )
        
        return City(
            id=city_data["id"],
            uuid=city_data["uuid"],
            name=city_data["name"],
            country=city_data["country"],
            language=city_data["language"],
            location=location,
            slug=city_data["slug"],
            search_volume=city_data["search_volume"],
            transportation_category=city_data["transportation_category"]
        )
    
    
    def __scrape_all_cities(self) -> List[ScrapedCity]:
        """
        Scrape all city names and slugs from flixbus.com/bus
        
        Returns:
            List of ScrapedCity objects containing name, slug, and starting letter
            
        Raises:
            Exception: If scraping fails
        """
        try:
            # Get the bus routes page
            response = self.session.get(
                f"{self.WEB_URL}/bus",
                headers={
                    **self.default_headers,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
                }
            )
            response.raise_for_status()
            
            # Parse the HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            cities: List[ScrapedCity] = []
            
            # Find all alphabet sections
            alphabet_sections = soup.find_all('div', class_='alphabet-item')
            
            for section in alphabet_sections:
                # Get the letter from the section title
                letter = section.find('h3', class_='alphabet-title').text.strip()
                
                # Find all city links in this section
                city_items = section.find_all('li', class_='alphabet-list-item')
                
                for item in city_items:
                    link = item.find('a')
                    if link:
                        name = link.text.strip()
                        # Extract slug from href (remove '/bus/' prefix)
                        slug = link['href'].replace('/bus/', '')
                        
                        cities.append(ScrapedCity(
                            name=name,
                            slug=slug,
                            letter=letter
                        ))
            
            return cities
            
        except requests.exceptions.RequestException as e:
            print(f"Failed to scrape cities: {str(e)}")
            raise
        except Exception as e:
            print(f"Error parsing HTML: {str(e)}")
            raise

    def get_cities_by_letter(self, letter: str) -> List[ScrapedCity]:
        """
        Get all cities starting with a specific letter
        
        Args:
            letter: Single letter to filter cities by
            
        Returns:
            List of ScrapedCity objects for that letter
        """
        cities = self.__scrape_all_cities()
        return [city for city in cities if city.letter.upper() == letter.upper()]

    def get_unique_city_letters(self) -> Set[str]:
        """
        Get all unique starting letters of cities
        
        Returns:
            Set of letters that cities start with
        """
        cities = self.__scrape_all_cities()
        return {city.letter for city in cities}
    
    def suggest_city(
        self,
        query: str,
        language: str = "en",
        country: str = "nl",
        flixbus_cities_only: bool = False,
        include_stations: bool = True,
        include_popular_stations: bool = True
    ) -> List[SearchResult]:
        """
        Search for cities by name and return weighted results
        
        Args:
            query: City name to search for
            language: Two-letter language code
            country: Two-letter country code
            flixbus_cities_only: Only return cities served by FlixBus
            include_stations: Include station information
            include_popular_stations: Include popular stations
            
        Returns:
            List of SearchResult objects, sorted by relevance
        """
        params = {
            "q": query,
            "lang": language,
            "country": country,
            "flixbus_cities_only": str(flixbus_cities_only).lower(),
            "stations": str(include_stations).lower(),
            "popular_stations": str(include_popular_stations).lower()
        }
        
        try:
            response = self._make_request("search/autocomplete/cities", params)
            
            # Parse results into SearchResult objects
            results = []
            for item in response:
                # Create Location object
                location = Location(
                    lat=item["location"]["lat"],
                    lon=item["location"]["lon"]
                )
                
                # Create Station objects
                stations = [
                    Station(
                        id=station["id"],
                        name=station["name"],
                        legacy_id=station["legacy_id"],
                        importance_order=station["importance_order"],
                        is_train=station["is_train"]
                    )
                    for station in item.get("stations", [])
                ]
                
                # Create SearchResult object
                result = SearchResult(
                    id=item["id"],
                    name=item["name"],
                    country=item["country"],
                    district=item.get("district"),
                    location=location,
                    score=item["score"],
                    legacy_id=item["legacy_id"],
                    stations=stations,
                    has_train_station=item["has_train_station"],
                    is_flixbus_city=item["is_flixbus_city"],
                    timezone_offset_seconds=item["timezone_offset_seconds"]
                )
                
                results.append(result)
            
            # Sort results by relevance score (descending)
            results.sort(key=lambda x: x.relevance, reverse=True)
            
            return results
            
        except Exception as e:
            print(f"Failed to search for city: {str(e)}")
            raise

    def get_best_match(self, query: str, language: str = "en", country: str = "de") -> Optional[SearchResult]:
        """
        Get the most relevant match for a city search
        
        Args:
            query: City name to search for
            language: Two-letter language code
            country: Two-letter country code
            
        Returns:
            Most relevant SearchResult or None if no matches found
        """
        results = self.suggest_city(query, language, country)
        return results[0] if results else None
    
    def get_search_analytics(
        self,
        from_city_id: str,
        to_city_id: str,
        start_date: datetime,
        end_date: datetime,
        granularity: str = "daily",  # Options: hourly, daily, weekly, monthly
        metrics: List[str] = None,
        currency: str = "EUR",
        locale: str = "en"
    ) -> Dict[str, Any]:
        """
        Get search analytics for a specific route
        
        Args:
            from_city_id: UUID of departure city
            to_city_id: UUID of arrival city
            start_date: Start date for analytics
            end_date: End date for analytics
            granularity: Time granularity (hourly, daily, weekly, monthly)
            metrics: List of metrics to retrieve (defaults to all)
            currency: Three-letter currency code
            locale: Two-letter locale code
            
        Returns:
            Dictionary containing search analytics data
        """
        if metrics is None:
            metrics = [
                "search_volume",
                "conversion_rate",
                "average_price",
                "occupancy_rate",
                "cancellation_rate",
                "mobile_searches",
                "desktop_searches"
            ]
        
        params = {
            "from_city_id": from_city_id,
            "to_city_id": to_city_id,
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
            "granularity": granularity,
            "metrics": ",".join(metrics),
            "currency": currency,
            "locale": locale
        }
        
        return self._make_request("search/service/v4/analytics", params)

    


if __name__ == "__main__":
    scraper = FlixBusScraper()
    
    # # Get cities in Netherlands   
    # cities = scraper.get_cities(language="nl", country="NL", limit=5)
    # print("Cities in Netherlands:")
    # for city in cities["result"]:
    #     city_obj = scraper.parse_city(city)
    #     print(f"- {city_obj.name} (UUID: {city_obj.uuid})")
    
    # # Get reachable cities from Amsterdam
    # amsterdam_id = "40dde3b8-8646-11e6-9066-549f350fcb0c"
    # reachable = scraper.get_reachable_cities(amsterdam_id, language="nl", country="NL", limit=5)
    # print("\nReachable cities from Amsterdam:")
    # for city in reachable["result"]:
    #     city_obj = scraper.parse_city(city)
    #     print(f"- {city_obj.name}")
    
    # Search for trips
    # from_city = "40dde3b8-8646-11e6-9066-549f350fcb0c"  # Amsterdam
    # to_city = "40dee83e-8646-11e6-9066-549f350fcb0c"    # Rotterdam
    # departure = datetime(2024, 10, 29)
    
    # trips = scraper.search_trips(
    #     from_city_id=from_city,
    #     to_city_id=to_city,
    #     departure_date=departure,
    #     num_adults=1,
    #     currency="EUR",
    #     locale="nl"
    # )
    # print("\nFound trips:", json.dumps(trips, indent=2))

    # Scrape all cities
    # print("Scraping all cities...")
    # cities = scraper.scrape_all_cities()
    
    # # Print some statistics
    # letters = scraper.get_unique_city_letters()
    # print(f"\nFound {len(cities)} cities across {len(letters)} letters")
    
    # # Print cities starting with 'A' as an example
    # a_cities = scraper.get_cities_by_letter('A')
    # print("\nCities starting with 'A':")
    # for city in a_cities:
    #     print(f"- {city.name} (slug: {city.slug})")

    # city_name = "Karlsruhe"
    # results = scraper.suggest_city(city_name, language="nl", country="nl")
    # best_match = scraper.get_bestg_match()
    
    # print(f"\nSearch results for '{city_name}':")
    # for result in results:
    #     print(f"\n{result.name} (Relevance: {result.relevance:.2f})")
    #     print(f"  ID: {result.id}")
    #     print(f"  Country: {result.country}")
    #     print(f"  District: {result.district or 'N/A'}")
    #     print(f"  FlixBus city: {result.is_flixbus_city}")
    #     print(f"  Has train station: {result.has_train_station}")
    #     print("  Stations:")    
    #     for station in result.stations:
    #         print(f"    - {station.name} ({'Train' if station.is_train else 'Bus'})")

    amsterdam_id = "40dde3b8-8646-11e6-9066-549f350fcb0c"
    berlin_id = "40d8f682-8646-11e6-9066-549f350fcb0c"
    start_date = datetime(2024, 1, 1)
    end_date = datetime(2024, 12, 31)
    
    # Get daily search analytics
    analytics = scraper.get_search_analytics(
        from_city_id=amsterdam_id,
        to_city_id=berlin_id,
        start_date=start_date,
        end_date=end_date,
        granularity="daily",
        metrics=["search_volume", "conversion_rate", "average_price"]
    )
    print("\nSearch Analytics:", json.dumps(analytics, indent=2))
    