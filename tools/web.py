"""
Web Search Tools - Fast (Tavily) + Deep Research (Perplexity)

Two modes:
- web_search: Fast search using Tavily (~0.5s) - for quick facts
- web_research: Deep research using Perplexity Sonar (~5s) - for complex questions
"""
import logging
import httpx
from typing import Optional, Dict, Any, List

from core.config import settings
from tools.registry import tool

logger = logging.getLogger("brainmap.tools.web")

# API URLs
TAVILY_API_URL = "https://api.tavily.com/search"
PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"


def is_search_available() -> bool:
    """Check if any search is available."""
    return bool(settings.TAVILY_API_KEY or settings.PERPLEXITY_API_KEY)


def is_tavily_available() -> bool:
    """Check if Tavily (fast search) is available."""
    return bool(settings.TAVILY_API_KEY)


def is_perplexity_available() -> bool:
    """Check if Perplexity (deep research) is available."""
    return bool(settings.PERPLEXITY_API_KEY)


@tool(
    name="web_search",
    description="""Fast web search using Tavily (~0.5s response time).

    USE THIS when:
    - User asks about current events, news, recent data
    - User wants factual info: prices, scores, lists, rankings
    - Quick lookups: "what is X", "who is Y", "when did Z happen"
    - Any query where you need fresh data fast

    For complex research questions requiring synthesis, use web_research instead."""
)
async def web_search(
    query: str,
    max_results: int = 5
) -> Dict[str, Any]:
    """
    Fast web search using Tavily.

    Args:
        query: Search query (be specific for better results)
        max_results: Number of results to return (1-10)

    Returns:
        Dict with 'answer', 'results' (title, url, content snippets)
    """
    # Fallback to Perplexity if Tavily not configured
    if not settings.TAVILY_API_KEY:
        if settings.PERPLEXITY_API_KEY:
            logger.info("Tavily not configured, falling back to Perplexity")
            return await web_research(query)
        return {
            "error": "Web search not configured",
            "message": "Add TAVILY_API_KEY to .env for fast search"
        }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                TAVILY_API_URL,
                json={
                    "api_key": settings.TAVILY_API_KEY,
                    "query": query,
                    "max_results": min(max_results, 10),
                    "include_answer": True,
                    "include_raw_content": False,
                    "search_depth": "basic"  # Fast mode
                }
            )

            if response.status_code != 200:
                logger.error(f"Tavily API error: {response.status_code} - {response.text[:200]}")
                # Fallback to Perplexity on error
                if settings.PERPLEXITY_API_KEY:
                    return await web_research(query)
                return {"error": f"Search failed: {response.status_code}"}

            data = response.json()

            # Format results
            results = []
            for r in data.get("results", []):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", "")[:500]  # Truncate
                })

            answer = data.get("answer", "")

            logger.info(f"Tavily search: '{query[:40]}...' -> {len(results)} results")

            return {
                "answer": answer,
                "results": results,
                "sources": [r["url"] for r in results[:3]],
                "provider": "tavily"
            }

    except httpx.TimeoutException:
        logger.warning("Tavily timeout, trying Perplexity")
        if settings.PERPLEXITY_API_KEY:
            return await web_research(query)
        return {"error": "Search timed out"}
    except Exception as e:
        logger.exception(f"Web search failed: {e}")
        return {"error": str(e)}


@tool(
    name="web_research",
    description="""Deep web research using Perplexity Sonar (~5s response time).

    USE THIS when:
    - Complex questions requiring synthesis from multiple sources
    - "Compare X vs Y", "What are the pros and cons of..."
    - Questions needing analysis, not just facts
    - Following up on web_search for deeper understanding

    For quick factual lookups, use web_search instead (10x faster)."""
)
async def web_research(
    query: str,
    detailed: bool = False
) -> Dict[str, Any]:
    """
    Deep research using Perplexity Sonar.

    Args:
        query: Research question (can be complex)
        detailed: If True, use sonar-pro for deeper analysis

    Returns:
        Dict with 'answer', 'citations'
    """
    if not settings.PERPLEXITY_API_KEY:
        return {
            "error": "Deep research not configured",
            "message": "Add PERPLEXITY_API_KEY to .env for research mode"
        }

    model = "sonar-pro" if detailed else "sonar"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                PERPLEXITY_API_URL,
                headers={
                    "Authorization": f"Bearer {settings.PERPLEXITY_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Be precise and concise. Provide factual information with specific details. Format lists clearly."
                        },
                        {
                            "role": "user",
                            "content": query
                        }
                    ],
                    "temperature": 0.0,
                    "return_citations": True
                }
            )

            if response.status_code != 200:
                logger.error(f"Perplexity error: {response.status_code}")
                return {"error": f"Research failed: {response.status_code}"}

            data = response.json()
            answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            citations = data.get("citations", [])

            logger.info(f"Perplexity research: '{query[:40]}...' -> {len(answer)} chars")

            return {
                "answer": answer,
                "citations": citations,
                "sources": citations[:5],
                "provider": "perplexity",
                "model": model
            }

    except httpx.TimeoutException:
        return {"error": "Research timed out (query may be too complex)"}
    except Exception as e:
        logger.exception(f"Research failed: {e}")
        return {"error": str(e)}


@tool(
    name="web_fetch",
    description="""Fetch and summarize content from a specific URL.

    USE THIS when:
    - User shares a URL and asks about its content
    - You need to read a specific webpage
    - Following up on a citation from web_search/web_research"""
)
async def web_fetch(
    url: str
) -> Dict[str, Any]:
    """
    Fetch and summarize URL content.

    Args:
        url: The URL to fetch

    Returns:
        Dict with 'content', 'url'
    """
    # Try Tavily extract first (faster)
    if settings.TAVILY_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://api.tavily.com/extract",
                    json={
                        "api_key": settings.TAVILY_API_KEY,
                        "urls": [url]
                    }
                )

                if response.status_code == 200:
                    data = response.json()
                    results = data.get("results", [])
                    if results:
                        content = results[0].get("raw_content", "")[:3000]
                        return {
                            "content": content,
                            "url": url,
                            "provider": "tavily"
                        }
        except Exception as e:
            logger.warning(f"Tavily extract failed: {e}")

    # Fallback to Perplexity
    if settings.PERPLEXITY_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    PERPLEXITY_API_URL,
                    headers={
                        "Authorization": f"Bearer {settings.PERPLEXITY_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "sonar",
                        "messages": [
                            {
                                "role": "system",
                                "content": "Summarize the main content from the URL. Extract key facts."
                            },
                            {
                                "role": "user",
                                "content": f"Read and summarize: {url}"
                            }
                        ],
                        "temperature": 0.0
                    }
                )

                if response.status_code == 200:
                    data = response.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    return {
                        "content": content,
                        "url": url,
                        "provider": "perplexity"
                    }
        except Exception as e:
            logger.exception(f"Perplexity fetch failed: {e}")

    return {
        "error": "Could not fetch URL",
        "url": url,
        "message": "Configure TAVILY_API_KEY or PERPLEXITY_API_KEY"
    }


# Legacy function for backwards compatibility
def set_perplexity_key(api_key: str) -> None:
    """Deprecated: Use settings.PERPLEXITY_API_KEY instead."""
    pass


def is_available() -> bool:
    """Check if web search is available (any provider)."""
    return is_search_available()
