import logging
import asyncio
from typing import Dict, Any, Optional
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

class WebBrowserTool:
    """Advanced Headless Browser Tool for Flora to surf, fill forms, and register on websites."""
    
    def __init__(self, headless: bool = True):
        self.headless = headless

    async def fetch_page_content(self, url: str) -> Dict[str, Any]:
        """Navigate to a website, wait for render, and extract clean text content."""
        logger.info(f"Navigating to {url}...")
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            # Use stealth-like user agent to prevent basic bot blocks
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            page = await context.new_page()
            
            try:
                # Go to URL with 30s timeout, wait until network is idle
                await page.goto(url, wait_until="networkidle", timeout=30000)
                
                # Get raw HTML
                html = await page.content()
                title = await page.title()
                
                # Parse with BeautifulSoup to extract clean text
                soup = BeautifulSoup(html, "html.parser")
                
                # Remove script and style elements
                for script in soup(["script", "style", "meta", "link"]):
                    script.extract()
                    
                text_content = soup.get_text(separator="\n")
                # Clean up whitespace
                lines = (line.strip() for line in text_content.splitlines())
                chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                clean_text = "\n".join(chunk for chunk in chunks if chunk)
                
                logger.info(f"Successfully fetched page: '{title}' ({len(clean_text)} chars of text)")
                return {
                    "success": True,
                    "title": title,
                    "url": url,
                    "text": clean_text[:15000]  # Limit to 15k characters to prevent context overflow
                }
            except Exception as e:
                logger.error(f"Failed to navigate to {url}: {e}")
                return {"success": False, "error": str(e)}
            finally:
                await browser.close()

    async def automate_action(self, url: str, actions: list) -> Dict[str, Any]:
        """
        Execute custom UI actions (fill input, click button, wait) to register or scrape data.
        'actions' is a list of dicts:
        [
            {"type": "fill", "selector": "#email", "value": "test@example.com"},
            {"type": "fill", "selector": "#password", "value": "SecurePass123!"},
            {"type": "click", "selector": "button[type='submit']"},
            {"type": "wait", "timeout": 5000}
        ]
        """
        logger.info(f"Starting browser automation on {url}...")
        
        # Defensive check: if the LLM wrapped actions inside an extra nested list (e.g. [[...]])
        if isinstance(actions, list) and len(actions) == 1 and isinstance(actions[0], list):
            logger.info("Defensively unwrapping nested list of actions")
            actions = actions[0]
            
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                
                for i, action in enumerate(actions):
                    a_type = action.get("type")
                    selector = action.get("selector")
                    value = action.get("value")
                    
                    logger.info(f"Executing step {i+1}: {a_type} on '{selector}'")
                    
                    if a_type == "fill":
                        await page.wait_for_selector(selector, state="visible", timeout=10000)
                        await page.fill(selector, value)
                    elif a_type == "click":
                        await page.wait_for_selector(selector, state="visible", timeout=10000)
                        await page.click(selector)
                    elif a_type == "wait":
                        timeout = action.get("timeout", 2000)
                        await page.wait_for_timeout(timeout)
                    elif a_type == "press":
                        key = action.get("key")
                        await page.press(selector, key)
                        
                # Wait a bit after all actions for page to update
                await page.wait_for_timeout(3000)
                
                final_url = page.url
                final_title = await page.title()
                html = await page.content()
                
                soup = BeautifulSoup(html, "html.parser")
                text_content = soup.get_text(separator="\n")
                clean_text = "\n".join(line.strip() for line in text_content.splitlines() if line.strip())
                
                logger.info(f"Automation finished. Final URL: {final_url}")
                return {
                    "success": True,
                    "final_url": final_url,
                    "final_title": final_title,
                    "text": clean_text[:10000]
                }
                
            except Exception as e:
                logger.error(f"Error during browser automation: {e}")
                return {"success": False, "error": str(e)}
            finally:
                await browser.close()

    async def search_web(self, query: str) -> Dict[str, Any]:
        """Search the web for a query using DuckDuckGo HTML version and return a list of matching search results (title, link, snippet)."""
        logger.info(f"Searching web for query: '{query}'...")
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            try:
                # DuckDuckGo HTML version is very clean and easy to scrape
                url = f"https://html.duckduckgo.com/html/?q={query}"
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                
                html = await page.content()
                soup = BeautifulSoup(html, "html.parser")
                
                results = []
                for result in soup.select(".result"):
                    title_elem = result.select_one(".result__title")
                    snippet_elem = result.select_one(".result__snippet")
                    
                    if title_elem and snippet_elem:
                        title = title_elem.get_text().strip()
                        snippet = snippet_elem.get_text().strip()
                        # Get URL
                        a_elem = title_elem.select_one("a")
                        href = a_elem["href"] if a_elem else ""
                        
                        # Handle proxy redirect urls from duckduckgo
                        if href.startswith("//"):
                            href = "https:" + href
                        elif "uddg=" in href:
                            # Extract actual URL if redirected
                            from urllib.parse import unquote, urlparse, parse_qs
                            parsed = urlparse(href)
                            qs = parse_qs(parsed.query)
                            if "uddg" in qs:
                                href = qs["uddg"][0]
                                
                        results.append({
                            "title": title,
                            "url": href,
                            "snippet": snippet
                        })
                        
                logger.info(f"Successfully found {len(results)} search results for '{query}'")
                return {
                    "success": True,
                    "query": query,
                    "results": results[:8]  # Return top 8 search results
                }
            except Exception as e:
                logger.error(f"Failed to search web for '{query}': {e}")
                return {"success": False, "error": str(e)}
            finally:
                await browser.close()
