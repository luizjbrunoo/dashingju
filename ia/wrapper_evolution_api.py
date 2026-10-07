import os
import requests
from urllib.parse import urlencode, urljoin

class EvolutionAPI:
    def __init__(self):
        self.BASE_URL = os.environ.get("EVOLUTION_API_BASE_URL") or ""
        self._API_KEY = {
            "dashing": os.environ.get("EVOLUTION_INSTANCE_KEY") or "",
        }

    def _send_request(
        self,
        path,
        method='GET',
        body=None,
        headers={},
        params_url={}
    ):

        method.upper()
        url = self._mount_url(path, params_url)

        if not isinstance(headers, dict):
            headers = {}

        headers.setdefault('Content-Type', 'application/json')
        instance = self._API_KEY.get('dashing')

        request = {
            'GET' : requests.get,
            'POST' : requests.post,
            'PUT' : requests.put,
            'DELETE' : requests.delete,

        }

        return request (method, url, headers=headers, json=body)


    def _mount_url(self, path, params_url):
        if isinstance(params_url, dict):
            params_url = urlencode(params_url)

        url = urljoin(self.BASE_URL, path)

        if params_url:
            url = url + '?' + params_url

        return url

    def get_token(self):
        email = os.environ.get("EVOLUTION_LOGIN_EMAIL") or ""
        password = os.environ.get("EVOLUTION_LOGIN_PASSWORD") or ""
        response = requests.post(
            urljoin(self.base_url, "api/v1/auth/login"),
            json={"email": email, "password": password},
        )
        return response.json()['access_token']

    def get_user(self):
        response = requests.get(urljoin(self.base_url, 'api/v1/users'), headers={'Authorization': f'Bearer {self.get_token()}'})
        return response.json()

class SendMessage(EvolutionAPI):
    def send_message(self, instance, body):
        path = f'/message/sendText/{instance}/'
        return self._send_request(path, method='POST', body=body)
