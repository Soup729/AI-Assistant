from typing import Any, Dict, List

import httpx
import requests

from app.storage.config import config_manager
from app.utils.logger import logger


class SearchService:
    def __init__(self):
        self.base_url = "https://api.tavily.com/search"

    def _payload(self, query: str, max_results: int) -> Dict[str, Any]:
        return {
            "api_key": (config_manager.config.search_api_key or "").strip(),
            "query": query,
            "search_depth": "basic",
            "max_results": max_results,
        }

    async def asearch(self, query: str, max_results: int = 3) -> List[Dict[str, str]]:
        api_key = (config_manager.config.search_api_key or "").strip()
        if not api_key:
            logger.warning("未配置搜索 API Key，跳过联网检索")
            return []

        payload = self._payload(query, max_results)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self.base_url, json=payload)
                response.raise_for_status()
                data = response.json()
            results = data.get("results", [])
            logger.info(f"联网检索完成，返回 {len(results)} 条结果")
            return results
        except Exception as e:
            logger.error(f"联网检索失败: {e}")
            return []

    def search(self, query: str, max_results: int = 3) -> List[Dict[str, str]]:
        api_key = (config_manager.config.search_api_key or "").strip()
        if not api_key:
            logger.warning("未配置搜索 API Key，跳过联网检索")
            return []

        payload = self._payload(query, max_results)
        try:
            response = requests.post(self.base_url, json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            logger.info(f"联网检索完成，返回 {len(results)} 条结果")
            return results
        except Exception as e:
            logger.error(f"联网检索失败: {e}")
            return []


search_service = SearchService()
