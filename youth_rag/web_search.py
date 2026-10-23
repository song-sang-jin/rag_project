"""웹 검색 결과를 후보 문서의 제목·요약·URL로 정규화한다."""
import os
import requests
from .config import settings
from .http import request


class WebSearch:
    def available(self):
        return bool(os.getenv("TAVILY_API_KEY", "").strip())

    def search(self, query):
        key = os.getenv("TAVILY_API_KEY", "").strip()
        if not key:
            raise ValueError("웹 검색에는 .env의 TAVILY_API_KEY가 필요합니다.")
        cfg = settings()
        try:
            response = request(requests, "post",
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {key}"},
                json={"query": query, "max_results": cfg.get("web_max_results", 10),
                      "search_depth": "basic", "include_answer": False,
                      "include_domains": cfg.get("web_domains", [])},
                timeout=(3, cfg.get("web_search_timeout", 6)),
            )
            response.raise_for_status()
            results = response.json()["results"]
        except (requests.RequestException, ValueError, KeyError, RuntimeError):
            raise RuntimeError("웹 검색에 실패했습니다. Tavily 키·한도·연결을 확인하세요.") from None
        return [{"title": str(r.get("title", "")), "summary": str(r.get("content", "")),
                 "url": str(r["url"])} for r in results if r.get("url")]
