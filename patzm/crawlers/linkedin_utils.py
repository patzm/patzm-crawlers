import configparser
import getpass
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
    def __init__(self, config_dir: str, cache_dir: str, login: Optional[str] = None, headless: bool = True):
        """A selenium-based interface to browse LinkedIn.

        Parameters
        ----------
        config_dir
            A path to the configuration files.
        cache_dir
            A path to the cache files.
        login, optional
            Configures the login behavior. Options are:

            - ``None``: uses cookies 🍪.
            - An empty string: the user will be prompted for the login credentials. This is the most secure way.
              After that, cookies 🍪 can be used.
            - A path to a credentials file: reads the credentials from this file. If it doesn't exist, a template
              empty file gets created. After that, cookies 🍪 can be used.
        headless
            Whether the web browser window (Firefox) is headless (no GUI).
        """
        self.driver = None
        
        firefox_options = webdriver.FirefoxOptions()
        if headless:
            firefox_options.add_argument("-headless")
        self.driver = webdriver.Firefox(options=firefox_options)
        self.driver.get("https://www.linkedin.com/")

        self._config_dir = config_dir
        self._cache_dir = cache_dir
        self._username_pattern = re.compile(r"https://www\.linkedin\.com/in/([^/]+)/?")

        if self.activate_session(login):
            print("Login / session activation successful 🎉")
        else:
            raise RuntimeError("Login to LinkedIn unsuccessful 😔")

    def __del__(self):
        self.close()

    def activate_session(self, login: str) -> bool:
        cookies_file = os.path.join(self._cache_dir, "linkedin_cookies.pkl")
        use_cookies = login is None and os.path.exists(cookies_file)
        if use_cookies:
            self._load_cookies(cookies_file)
            if self.validate_login():
                return True

        credentials = self._get_login_credentials(login)
        self.login(credentials)
        if self.validate_login():
            self._save_cookies(cookies_file)
            return True
        else:
            return False

    def close(self):
        if self.driver is not None:
            self.driver.close()
            self.driver.quit()
            self.driver = None
        
    def login(self, credentials: configparser.ConfigParser):
        login_url = "https://www.linkedin.com/login"
        self.driver.get(login_url)
        if self.driver.current_url != login_url and self.validate_login():
            print("Already logged in 🙄")
            return

        username_field = self.driver.find_element(by=By.ID, value="username")
        username_field.clear()
        username_field.send_keys(credentials["linkedin"]["username"])

        password_field = self.driver.find_element(by=By.ID, value="password")
        password_field.clear()
        password_field.send_keys(credentials["linkedin"]["password"])

        login_button = self.driver.find_element(by=By.CSS_SELECTOR, value="[type='submit']")
        login_button.click()

        while "https://www.linkedin.com/checkpoint" in self.driver.current_url:
            input(f"User authenticity validation required. Please press enter when done.")

        if self.driver.current_url == "https://www.linkedin.com/check/manage-account":
            input(f"The account management dialog isn't implemented. Please manually approve it.")
        
    def validate_login(self) -> bool:
        self.driver.get("https://www.linkedin.com/mynetwork/")
        self.wait_for(By.CLASS_NAME, "mn-community-summary")
        success = self.driver.current_url == "https://www.linkedin.com/mynetwork/"
        return success

    def _get_login_credentials(self, login: Optional[str] = None) -> configparser.ConfigParser:
        def _get_config_parser(username: str, password: str) -> configparser.ConfigParser:
            credentials = configparser.ConfigParser()
            credentials["linkedin"] = {}
            credentials["linkedin"]["username"] = username
            credentials["linkedin"]["password"] = password
            return credentials

        if login == "":
            username = input("Provide your LinkedIn username:")
            password = getpass.getpass("Provide your LinkedIn password:")
            return _get_config_parser(username, password)
        else:
            if login is None:
                login = os.path.join(self._config_dir, "credentials.ini")

            if not os.path.exists(login):
                credentials = _get_config_parser("your@email.com", "your-password")
                with open(login, "w") as credentials_file:
                    credentials.write(credentials_file)

                print(f"The template credentials file has been written to {login}. Fill it.")
                exit(0)
            else:
                credentials = configparser.ConfigParser()
                credentials.read(login)
                return credentials

    def _load_cookies(self, cookies_file: str):
        with open(cookies_file, "rb") as file:
            cookies = pickle.load(file)
            for cookie in cookies:
                self.driver.add_cookie(cookie)

    def _save_cookies(self, cookies_file: str):
        with open(cookies_file, "wb") as file:
            pickle.dump(self.driver.get_cookies(), file)

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
    url = f"https://www.linkedin.com/search/results/companies/?keywords={name}"
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
