import configparser
import os
import pickle
import re
from typing import List, Optional, Tuple

import Levenshtein
from pydantic import BaseModel, Field
from selenium import webdriver
from selenium.common import exceptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions, wait


class LinkedInProvider:
    def __init__(self, config_dir: str, cache_dir: str):
        self.driver = webdriver.Firefox()
        self.driver.get("https://linkedin.com/")

        self._config_dir = config_dir
        self._cache_dir = cache_dir

        cookies_file = os.path.join(cache_dir, "linkedin_cookies.pkl")
        login_required = True
        if os.path.exists(cookies_file):
            with open(cookies_file, "rb") as file:
                cookies = pickle.load(file)
                for cookie in cookies:
                    self.driver.add_cookie(cookie)
            self.driver.get("https://www.linkedin.com/mynetwork/")
            login_required = self.driver.current_url != "https://www.linkedin.com/mynetwork/"

        if login_required:
            credentials = self._get_login_credentials()
            self.login(credentials)
            with open(cookies_file, "wb") as file:
                pickle.dump(self.driver.get_cookies(), file)

        self._username_pattern = re.compile(r"https://www\.linkedin\.com/in/([^/]+)/?")

    def login(self, credentials: configparser.ConfigParser):
        self.driver.get("https://linkedin.com/login")

        username_field = self.driver.find_element(by=By.ID, value="username")
        username_field.clear()
        username_field.send_keys(credentials["linkedin"]["username"])

        password_field = self.driver.find_element(by=By.ID, value="password")
        password_field.clear()
        password_field.send_keys(credentials["linkedin"]["password"])

        login_button = self.driver.find_element(by=By.CSS_SELECTOR, value="[type='submit']")
        login_button.click()

        if self.driver.current_url == "https://www.linkedin.com/check/manage-account":
            raise NotImplementedError(f"The account management dialog isn't implemented. Please manually approve it.")

    def _get_login_credentials(self) -> configparser.ConfigParser:
        credentials_file_path = os.path.join(self._config_dir, "credentials.ini")
        credentials = configparser.ConfigParser()
        if not os.path.exists(credentials_file_path):
            credentials["linkedin"] = {}
            credentials["linkedin"]["username"] = "your@email.com"
            credentials["linkedin"]["password"] = "your-password"

            with open(credentials_file_path, "w") as credentials_file:
                credentials.write(credentials_file)

            print(f"The template credentials file has been written to {credentials_file_path}. Fill it.")
            exit(0)
        else:
            credentials.read(credentials_file_path)
            return credentials

    def __del__(self):
        self.driver.close()

    def get_username_from_url(self, url: str) -> Optional[str]:
        match = self._username_pattern.search(url)
        return match.group(1) if match else None

    def get_company_name_from_id(self, company_id: int) -> str:
        pass

    def wait_for(self, by: str, value: str, timeout: float = 5.0) -> bool:
        try:
            element_present = expected_conditions.presence_of_element_located((by, value))
            wait.WebDriverWait(self.driver, timeout=timeout).until(element_present)
            return True
        except exceptions.TimeoutException:
            return False


class Company(BaseModel):
    name: str
    url: str
    ids: List[str] = Field(default_factory=list)


def search_company(li_provider: LinkedInProvider, name: str) -> List[Company]:
    url = f"https://www.linkedin.com/search/results/companies/?" f"keywords={name}"
    li_provider.driver.get(url=url)
    if not li_provider.wait_for(by=By.CLASS_NAME, value="entity-result__title-text"):
        return list()

    results = li_provider.driver.find_elements(by=By.CLASS_NAME, value="entity-result__title-text")

    companies = list()
    for result in results:
        link = result.find_element(By.XPATH, "a")
        company = Company(name=link.text, url=link.get_attribute("href"))
        companies.append(company)

    company_id_pattern = re.compile(r"%22(\d+)%22")
    for company in companies:
        li_provider.driver.get(company.url)
        li_provider.wait_for(by=By.CLASS_NAME, value="org-top-card-summary-info-list__info-item")
        company_infos = li_provider.driver.find_elements(
            by=By.CLASS_NAME, value="org-top-card-summary-info-list__info-item"
        )
        for ci in company_infos:
            if ci.tag_name == "a":
                employee_search_url = ci.get_attribute("href")
                company_ids = list(company_id_pattern.findall(employee_search_url))
                company.ids = company_ids
                break

    return companies


def search_profile(li_provider: LinkedInProvider, name: str, company_codes: List[int]) -> Tuple[Optional[str], float]:
    company_encoding = ",".join(f'"{c}"' for c in company_codes)

    url = (
        f"https://www.linkedin.com/search/results/people/?"
        f"keywords={name}"
        f"&origin=SPELL_CHECK_REPLACE"
        f"&sid=(ol&spellCorrectionEnabled=false"
    )

    present = f"&currentCompany=[{company_encoding}]"
    past = f"&pastCompany=[{company_encoding}]"
    for employment in (present, past):
        query_url = url + employment
        li_provider.driver.get(query_url)
        results = li_provider.driver.find_elements(by=By.CLASS_NAME, value="entity-result")
        if len(results) == 0:
            continue

        # navigate to the first search result
        first_result_link = results[0].find_element(by=By.TAG_NAME, value="a")
        first_result_link.click()
        if not li_provider.wait_for(by=By.ID, value="profile-content"):
            continue

        # Validate name
        heading_elements = li_provider.driver.find_elements(by=By.TAG_NAME, value="h1")
        match = False
        match_rating = 0
        for he in heading_elements:
            if "text-heading-xlarge" in he.get_attribute("class"):
                match_rating = Levenshtein.ratio(he.text, name)
                match = match_rating > 0.3
                if match:
                    break

        if not match:
            continue

        profile_url = li_provider.driver.current_url
        return profile_url, match_rating
    return None, 0
