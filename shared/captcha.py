from __future__ import annotations

import time
from typing import Any, Optional

import requests

from shared.log import get_logger

logger = get_logger(__name__)


class YesCaptchaClient:
    def __init__(self, client_key: str, use_china_endpoint: bool = False):
        if not client_key:
            raise ValueError("captcha.client_key 为空")
        self.client_key = client_key
        self.base_url = (
            "https://cn.yescaptcha.com"
            if use_china_endpoint
            else "https://api.yescaptcha.com"
        )
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def create_task(
        self,
        website_url: str,
        website_key: str,
        task_type: str = "NoCaptchaTaskProxyless",
        is_invisible: bool = False,
    ) -> Optional[str]:
        task: dict[str, Any] = {
            "type": task_type,
            "websiteURL": website_url,
            "websiteKey": website_key,
        }
        if is_invisible:
            task["isInvisible"] = True
        payload = {"clientKey": self.client_key, "task": task}
        resp = self.session.post(f"{self.base_url}/createTask", json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errorId") == 0:
            task_id = data.get("taskId")
            logger.info("[captcha] task created: %s", task_id)
            return str(task_id) if task_id is not None else None
        logger.error(
            "[captcha] create failed: %s", data.get("errorDescription")
        )
        return None

    def get_task_result(self, task_id: str) -> Optional[str]:
        payload = {"clientKey": self.client_key, "taskId": task_id}
        resp = self.session.post(
            f"{self.base_url}/getTaskResult", json=payload, timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("errorId") != 0:
            logger.error("[captcha] poll error: %s", data.get("errorDescription"))
            return None
        if data.get("status") == "ready":
            token = (data.get("solution") or {}).get("gRecaptchaResponse")
            return str(token) if token else None
        return None

    def solve(
        self,
        website_url: str,
        website_key: str,
        timeout: float = 90.0,
        poll_interval: float = 3.0,
    ) -> Optional[str]:
        task_id = self.create_task(website_url, website_key)
        if not task_id:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            token = self.get_task_result(task_id)
            if token:
                logger.info("[captcha] token ok len=%s", len(token))
                return token
            time.sleep(poll_interval)
        logger.error("[captcha] timeout")
        return None
