import asyncio
from typing import List

import httpx
import requests
from bs4 import BeautifulSoup

from app.utils.logger import logger

try:
    import trafilatura

    _HAS_TRAFILA = True
except ImportError:
    _HAS_TRAFILA = False


class ContentExtractor:
    def __init__(self):
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            )
        }

    def _extract_text_from_html(self, html: str) -> str:
        if _HAS_TRAFILA:
            try:
                content = trafilatura.extract(html, include_links=False, include_images=False)
                if content:
                    return content.strip()
            except Exception:
                pass

        soup = BeautifulSoup(html, "html.parser")
        for node in soup(["script", "style", "nav", "footer", "header"]):
            node.decompose()
        return soup.get_text(separator="\n", strip=True)

    async def afetch_url_content(self, url: str) -> str:
        try:
            logger.info(f"抓取网页内容: {url}")
            async with httpx.AsyncClient(headers=self.headers, timeout=10.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                html = response.text
            text = await asyncio.to_thread(self._extract_text_from_html, html)
            return text
        except Exception as e:
            logger.error(f"抓取网页失败 ({url}): {e}")
            return ""

    def fetch_url_content(self, url: str) -> str:
        try:
            logger.info(f"抓取网页内容: {url}")
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            response.encoding = response.apparent_encoding
            return self._extract_text_from_html(response.text)
        except Exception as e:
            logger.error(f"抓取网页失败 ({url}): {e}")
            return ""

    async def aget_summarized_context(self, urls: List[str], limit_char: int = 2000) -> str:
        if not urls:
            return ""
        tasks = [self.afetch_url_content(url) for url in urls]
        contents = await asyncio.gather(*tasks, return_exceptions=True)

        parts = []
        for url, content in zip(urls, contents):
            if isinstance(content, Exception):
                continue
            if content:
                parts.append(f"Source URL: {url}\nContent: {content[:1000]}...")
        merged = "\n\n---\n\n".join(parts)
        return merged[:limit_char]

    def get_summarized_context(self, urls: List[str], limit_char: int = 2000) -> str:
        all_contents = []
        for url in urls:
            content = self.fetch_url_content(url)
            if content:
                all_contents.append(f"Source URL: {url}\nContent: {content[:1000]}...")
        context = "\n\n---\n\n".join(all_contents)
        return context[:limit_char]


content_extractor = ContentExtractor()
