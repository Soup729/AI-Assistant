import requests
from typing import List, Dict, Any
from app.storage.config import config
from app.utils.logger import logger

class SearchService:
    def __init__(self):
        # 默认使用 Tavily API (https://tavily.com/)
        self.api_key = config.search_api_key
        self.base_url = "https://api.tavily.com/search"

    def search(self, query: str, max_results: int = 3) -> List[Dict[str, str]]:
        """
        进行联网搜索，返回结果列表 [{title, url, content}]
        """
        if not self.api_key:
            logger.warning("未配置搜索 API Key，跳过联网检索")
            return []

        payload = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": max_results
        }
        
        try:
            response = requests.post(self.base_url, json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            logger.info(f"搜索完成，获取到 {len(results)} 条结果")
            return results
        except Exception as e:
            logger.error(f"搜索请求失败: {e}")
            return []

search_service = SearchService()
