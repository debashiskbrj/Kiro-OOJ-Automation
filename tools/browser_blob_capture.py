r"""Capture browser-generated blob downloads inside the page instead of via Edge.

QuickSight generates CSV exports as blob: URLs and, on this managed Edge build, sometimes
tears down the entire automation context milliseconds after the download event. This hook
intercepts programmatic <a download>.click(), capture-phase anchor clicks, and window.open
for blob URLs, reads the Blob as text, and prevents the native download. The caller writes
that text directly to disk. No network/auth data is exposed.
"""
import time


def install(page) -> None:
    page.evaluate("""
    () => {
      window.__oojBlobCapture = null;
      window.__oojBlobCaptureError = null;
      window.__oojCaptureBlobUrl = async (url, filename) => {
        if (window.__oojBlobCapture && window.__oojBlobCapture.text) return;
        try {
          const response = await fetch(url);
          const text = await response.text();
          window.__oojBlobCapture = {filename: filename || 'export.csv', text, size: text.length};
        } catch (error) {
          window.__oojBlobCaptureError = String(error);
        }
      };
      if (window.__oojBlobCaptureInstalled) return;
      window.__oojBlobCaptureInstalled = true;

      const nativeClick = HTMLAnchorElement.prototype.click;
      HTMLAnchorElement.prototype.click = function(...args) {
        const href = this.href || '';
        if (href.startsWith('blob:')) {
          window.__oojCaptureBlobUrl(href, this.download || 'export.csv');
          return;
        }
        return nativeClick.apply(this, args);
      };

      document.addEventListener('click', event => {
        const anchor = event.target && event.target.closest ? event.target.closest('a') : null;
        if (anchor && (anchor.href || '').startsWith('blob:')) {
          event.preventDefault();
          event.stopImmediatePropagation();
          window.__oojCaptureBlobUrl(anchor.href, anchor.download || 'export.csv');
        }
      }, true);

      const nativeOpen = window.open;
      window.open = function(url, ...args) {
        if (String(url || '').startsWith('blob:')) {
          window.__oojCaptureBlobUrl(String(url), 'export.csv');
          return null;
        }
        return nativeOpen.call(window, url, ...args);
      };
    }
    """)


def wait(page, timeout_s: int = 360, log=print) -> dict | None:
    """Wait for captured {filename,text,size}; return None if the page/context closes."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            result = page.evaluate("() => window.__oojBlobCapture")
            error = page.evaluate("() => window.__oojBlobCaptureError")
        except Exception as exc:
            log(f"blob capture page closed before content was read: {str(exc)[:100]}")
            return None
        if result and result.get("text") is not None:
            log(f"blob captured in page: {result.get('filename')} ({result.get('size')} chars)")
            return result
        if error:
            raise RuntimeError(f"blob capture failed: {error}")
        time.sleep(1)
    return None
