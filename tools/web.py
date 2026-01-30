"""
Web Search Tool - Perplexity Sonar API

Provides real-time web search capabilities using Perplexity's Sonar model.
Returns grounded, citation-backed answers from the internet.
"""
import logging
import httpx
from typing import Optional, Dict, Any, List

from tools.registry import tool

logger = logging.getLogger("brainmap.tools.web")

# Perplexity API configuration
PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"
PERPLEXITY_MODEL = "sonar"  # Lightweight, fast, cost-effective

_api_key: Optional[str] = None


def set_perplexity_key(api_key: str) -> None:
    """Set the Perplexity API key."""
    global _api_key
    _api_key = api_key


def is_available() -> bool:
    """Check if web search is available."""
    return _api_key is not None


@tool(
    name="web_search",
    description="""Search the web for real-time information using Perplexity AI.
    
    USE THIS when:
    - User asks about current events, news, recent releases
    - User wants factual data you don't have (movie lists, prices, sports scores)
    - User asks "what are the top/best/latest X"
    - Any query where your training data might be stale
    
    Returns a grounded answer with citations from the web."""
)
async def web_search(
    query: str,
    detailed: bool = False
) -> Dict[str, Any]:
    """
    Search the web for information.
    
    Args:
        query: The search query (be specific for better results)
        detailed: If True, use sonar-pro for more in-depth research
        
    Returns:
        Dict with 'answer', 'citations', and 'sources'
    """
    if not _api_key:
        return {
            "error": "Web search not configured",
            "message": "Perplexity API key not set. Add PERPLEXITY_API_KEY to .env"
        }
    
    model = "sonar-pro" if detailed else "sonar"
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                PERPLEXITY_API_URL,
                headers={
                    "Authorization": f"Bearer {_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Be precise and concise. Provide factual information with specific details like names, dates, and numbers. Format lists clearly."
                        },
                        {
                            "role": "user",
                            "content": query
                        }
                    ],
                    "temperature": 0.0,  # More factual
                    "return_citations": True
                }
            )
            
            if response.status_code != 200:
                logger.error(f"Perplexity API error: {response.status_code} - {response.text}")
                return {
                    "error": f"Search failed: {response.status_code}",
                    "message": response.text[:200]
                }
            
            data = response.json()
            
            # Extract the answer
            answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            # Extract citations if available
            citations = data.get("citations", [])
            
            logger.info(f"Web search completed: '{query[:50]}...' -> {len(answer)} chars, {len(citations)} citations")
            
            return {
                "answer": answer,
                "citations": citations,
                "sources": citations[:5],  # Top 5 sources for display
                "model_used": model
            }
            
    except httpx.TimeoutException:
        logger.error(f"Perplexity API timeout for query: {query}")
        return {
            "error": "Search timed out",
            "message": "The web search took too long. Try a simpler query."
        }
    except Exception as e:
        logger.exception(f"Web search failed: {e}")
        return {
            "error": "Search failed",
            "message": str(e)
        }


@tool(
    name="web_fetch",
    description="""Fetch and read content from a specific URL.
    
    USE THIS when:
    - User shares a URL and asks about its content
    - You need to read a specific webpage
    - Following up on a citation from web_search
    
    Returns the main text content from the URL."""
)
async def web_fetch(
    url: str,
    extract_type: str = "text"
) -> Dict[str, Any]:
    """
    Fetch content from a URL.
    
    Args:
        url: The URL to fetch
        extract_type: 'text' for main content, 'full' for everything
        
    Returns:
        Dict with 'content', 'title', 'url'
    """
    if not _api_key:
        return {
            "error": "Web fetch not configured",
            "message": "Perplexity API key not set"
        }
    
    # Use Perplexity to summarize the URL content
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                PERPLEXITY_API_URL,
                headers={
                    "Authorization": f"Bearer {_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "sonar",
                    "messages": [
                        {
                            "role": "system",
                            "content": "Summarize the main content from the provided URL. Extract key information, facts, and details."
                        },
                        {
                            "role": "user",
                            "content": f"Read and summarize this URL: {url}"
                        }
                    ],
                    "temperature": 0.0
                }
            )
            
            if response.status_code != 200:
                return {
                    "error": f"Fetch failed: {response.status_code}",
                    "url": url
                }
            
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            return {
                "content": content,
                "url": url,
                "source": "perplexity_sonar"
            }
            
    except Exception as e:
        logger.exception(f"Web fetch failed for {url}: {e}")
        return {
            "error": "Fetch failed",
            "url": url,
            "message": str(e)
        }
