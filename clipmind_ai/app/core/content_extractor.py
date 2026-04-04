import requests
from bs4 import BeautifulSoup
from app.utils.logger import logger

try:
    import trafilatura
    _has_trafilatura = True
except ImportError:
    _has_trafilatura = False

class ContentExtractor:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        }

    def fetch_url_content(self, url: str) -> str:
        """
        抓取并提取网页的正文部分。
        """
        try:
            logger.info(f"正在抓取网页内容: {url}")
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            # 设置正确的编码
            response.encoding = response.apparent_encoding
            html = response.text
            
            # 优先使用 trafilatura 提取正文
            if _has_trafilatura:
                content = trafilatura.extract(html, include_links=False, include_images=False)
                if content:
                    logger.debug(f"通过 trafilatura 提取成功，内容长度: {len(content)}")
                    return content.strip()
            
            # 降级：使用 BeautifulSoup 提取所有文本（可能包含大量噪声）
            soup = BeautifulSoup(html, "lxml") if "lxml" in html else BeautifulSoup(html, "html.parser")
            
            # 去除无用标签
            for script_or_style in soup(["script", "style", "nav", "footer", "header"]):
                script_or_style.decompose()
            
            text = soup.get_text(separator="\n", strip=True)
            logger.debug(f"通过 BeautifulSoup 提取完成，内容长度: {len(text)}")
            return text
            
        except Exception as e:
            logger.error(f"抓取网页失败 ({url}): {e}")
            return ""

    def get_summarized_context(self, urls: list, limit_char: int = 2000) -> str:
        """
        抓取多个 URL 并整合为上下文。
        """
        all_contents = []
        for url in urls:
            content = self.fetch_url_content(url)
            if content:
                all_contents.append(f"Source URL: {url}\nContent: {content[:1000]}...")
        
        context = "\n\n---\n\n".join(all_contents)
        return context[:limit_char]

content_extractor = ContentExtractor()
