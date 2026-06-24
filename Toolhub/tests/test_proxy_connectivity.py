import urllib.request
import logging

# Configure logging to see output
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ProxyTest")

def test_proxy_detection():
    print("--- Proxy Detection Test ---")
    proxies = urllib.request.getproxies()
    if proxies:
        print(f"System proxies detected: {proxies}")
    else:
        print("No system proxies detected.")

    print("\n--- Testing Opener with ProxyHandler ---")
    try:
        proxy_handler = urllib.request.ProxyHandler()
        opener = urllib.request.build_opener(proxy_handler)
        
        # Test URL (GitHub raw content for version.txt)
        url = "https://raw.githubusercontent.com/ashish45d/mlk-ii-alvt-updates/refs/heads/main/version.txt"
        
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'MLK-II-ALVT-ProxyTest/1.1'}
        )
        
        print(f"Connecting to: {url}...")
        response = opener.open(req, timeout=10)
        content = response.read().decode('utf-8').strip()
        print(f"Success! Remote content: {content}")
        
    except Exception as e:
        print(f"Connection failed: {e}")
        print("\nNote: If this fails in your ENVIRONMENT, it proves that either:")
        print("1. The proxy requires authentication which urllib cannot handle automatically.")
        print("2. The network blocks even proxy-aware traffic from non-browser apps.")
        print("3. There is no internet access even with proxy.")

if __name__ == "__main__":
    test_proxy_detection()
