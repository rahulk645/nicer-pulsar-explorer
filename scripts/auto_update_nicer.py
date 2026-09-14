import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# Switch to the canonical API endpoint
ARXIV_QUERY_URL = (
    "https://arxiv.org/api/query?search_query="
    "cat:astro-ph.HE+AND+(NICER+AND+(%22mass-radius%22+OR+%22pulse+profile%22+OR+%22X-PSI%22))"
    "&start=0&max_results=5&sortBy=submittedDate&sortOrder=descending"
)


def fetch_recent_arxiv_papers(max_retries=3):
  """Fetch recent papers from arXiv API with robust timeout & rate-limit handling."""
  print("Fetching recent preprints from arXiv API...")

  headers = {
      "User-Agent": (
          "NICER-Pulsar-Tracker/1.0 (Astrophysics Research Bot;"
          " mailto:academic-researcher@domain.edu)"
      ),
      "Accept": "application/atom+xml",
  }
  req = urllib.request.Request(ARXIV_QUERY_URL, headers=headers)

  xml_data = None
  delays = [15, 30, 45]  # Generous backoff for shared runner IPs

  for attempt in range(max_retries):
    try:
      # Increased timeout to 45s because arXiv can be slow to respond to cloud IPs
      with urllib.request.urlopen(req, timeout=45) as response:
        xml_data = response.read()
        break
    except urllib.error.HTTPError as e:
      if e.code == 429:
        wait_time = delays[attempt] if attempt < len(delays) else 60
        print(
            f"arXiv API returned 429. Backing off for {wait_time}s (attempt"
            f" {attempt + 1}/{max_retries})..."
        )
        time.sleep(wait_time)
      else:
        print(f"HTTP error: {e}")
        break
    except (TimeoutError, socket.timeout):
      wait_time = delays[attempt] if attempt < len(delays) else 60
      print(
          f"arXiv connection timed out. Backing off for {wait_time}s (attempt"
          f" {attempt + 1}/{max_retries})..."
      )
      time.sleep(wait_time)
    except Exception as e:
      print(f"Network error: {e}")
      time.sleep(10)

  if not xml_data:
    print(
        "Warning: Could not contact arXiv (rate-limited or timed out)."
        " Skipping this run without failing."
    )
    return []

  root = ET.fromstring(xml_data)
  ns = {"atom": "http://www.w3.org/2005/Atom"}

  papers = []
  for entry in root.findall("atom:entry", ns):
    title = entry.find("atom:title", ns).text.strip().replace("\n", " ")
    summary = entry.find("atom:summary", ns).text.strip().replace("\n", " ")
    paper_id = entry.find("atom:id", ns).text.strip()
    published = entry.find("atom:published", ns).text.strip()[:10]

    authors = [
        a.find("atom:name", ns).text for a in entry.findall("atom:author", ns)
    ]

    papers.append({
        "id": paper_id,
        "title": title,
        "summary": summary,
        "published": published,
        "authors": (
            ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        ),
    })

  print(f"Retrieved {len(papers)} candidate papers from arXiv.")
  return papers