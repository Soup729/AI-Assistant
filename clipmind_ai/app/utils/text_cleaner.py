import re

def clean_text(text: str) -> str:
    """
    清理文本中的多余空白字符和 HTML 标签。
    """
    if not text:
        return ""
    
    # 去除 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)
    
    # 将多个空格/换行替换为单个
    text = re.sub(r'\s+', ' ', text)
    
    return text.strip()
