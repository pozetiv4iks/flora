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
        """Search the web for a query using Yahoo Search as primary and DuckDuckGo HTML/Lite as backup."""
        logger.info(f"Searching web for query: '{query}'...")
        import httpx
        from urllib.parse import quote_plus, unquote
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache"
        }
        
        results = []
        
        # --- PHASE 1: Yahoo Search (Extremely reliable for VPS IPs) ---
        yahoo_url = f"https://search.yahoo.com/search?q={quote_plus(query)}"
        try:
            logger.info("Attempting Yahoo Search...")
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(yahoo_url, headers=headers)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "html.parser")
                    algo_blocks = soup.select(".algo")
                    for block in algo_blocks:
                        h3 = block.find("h3")
                        a_elem = block.find("a")
                        comp_text = block.select_one(".compText") or block.select_one(".fc-oxygen") or block.select_one(".fc-spine")
                        
                        if a_elem and h3:
                            title = h3.get_text().strip()
                            href = a_elem.get("href", "").strip()
                            snippet = comp_text.get_text().strip() if comp_text else ""
                            
                            # Clean Yahoo redirect URL format to extract direct target URL
                            if "/RU=" in href:
                                try:
                                    part = href.split("/RU=")[1]
                                    redirect_url = part.split("/RK=")[0]
                                    href = unquote(redirect_url)
                                except Exception:
                                    pass
                                    
                            if title and href:
                                results.append({
                                    "title": title,
                                    "url": href,
                                    "snippet": snippet
                                })
                                
                    if results:
                        logger.info(f"Successfully found {len(results)} search results via Yahoo Search")
                        return {
                            "success": True,
                            "query": query,
                            "results": results[:8]
                        }
        except Exception as e:
            logger.warning(f"Yahoo Search failed: {e}. Falling back to DuckDuckGo...")

        # --- PHASE 2: DuckDuckGo HTML Fallback ---
        ddg_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            logger.info("Attempting DuckDuckGo HTML search...")
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(ddg_url, headers=headers)
                
                # Fallback to lite version if blocked
                if r.status_code == 403 or "forbidden" in r.text.lower():
                    logger.warning("DuckDuckGo HTML returned 403 or forbidden. Falling back to DuckDuckGo Lite...")
                    lite_url = f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}"
                    r = await client.get(lite_url, headers=headers)
                
                r.raise_for_status()
                html = r.text
                soup = BeautifulSoup(html, "html.parser")
                
                # Parse HTML results
                html_results = soup.select(".result")
                if html_results:
                    for result in html_results:
                        title_elem = result.select_one(".result__title")
                        snippet_elem = result.select_one(".result__snippet")
                        
                        if title_elem and snippet_elem:
                            title = title_elem.get_text().strip()
                            snippet = snippet_elem.get_text().strip()
                            a_elem = title_elem.select_one("a")
                            href = a_elem["href"] if a_elem else ""
                            
                            if href.startswith("//"):
                                href = "https:" + href
                            elif "uddg=" in href:
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
                else:
                    # Parse Lite results
                    rows = soup.select("table tr")
                    for i, row in enumerate(rows):
                        link_elem = row.select_one(".result-link")
                        if link_elem:
                            title = link_elem.get_text().strip()
                            href = link_elem["href"] if link_elem.has_attr("href") else ""
                            
                            if href.startswith("//"):
                                href = "https:" + href
                            elif "uddg=" in href:
                                from urllib.parse import unquote, urlparse, parse_qs
                                parsed = urlparse(href)
                                qs = parse_qs(parsed.query)
                                if "uddg" in qs:
                                    href = qs["uddg"][0]
                                    
                            snippet = ""
                            if i + 1 < len(rows):
                                snippet_elem = rows[i+1].select_one(".result-snippet")
                                if snippet_elem:
                                    snippet = snippet_elem.get_text().strip()
                            
                            results.append({
                                "title": title,
                                "url": href,
                                "snippet": snippet
                            })
                
                if results:
                    logger.info(f"Successfully found {len(results)} search results via DuckDuckGo")
                    return {
                        "success": True,
                        "query": query,
                        "results": results[:8]
                    }
        except Exception as e:
            logger.error(f"DuckDuckGo search fallback failed: {e}")
            
        return {"success": False, "error": "Все доступные поисковые системы (Yahoo, DuckDuckGo) заблокировали запрос или не вернули результатов."}
