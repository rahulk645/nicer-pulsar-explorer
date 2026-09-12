import os
import json
import xml.etree.ElementTree as ET
import urllib.request
import urllib.parse
from google import genai

# Configuration
DATA_FILE_PATH = "pulsars_data.json"
ARXIV_QUERY_URL = (
    "http://export.arxiv.org/api/query?search_query="
    "cat:astro-ph.HE+AND+(NICER+AND+(%22mass-radius%22+OR+%22pulse+profile%22+OR+%22X-PSI%22))"
    "&start=0&max_results=5&sortBy=submittedDate&sortOrder=descending"
)

def fetch_recent_arxiv_papers():
    """Fetch recent papers from arXiv API."""
    print("Fetching recent preprints from arXiv API...")
    req = urllib.request.Request(ARXIV_QUERY_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        xml_data = response.read()
    
    root = ET.fromstring(xml_data)
    ns = {'atom': 'http://www.w3.org/2005/Atom'}
    
    papers = []
    for entry in root.findall('atom:entry', ns):
        title = entry.find('atom:title', ns).text.strip().replace('\n', ' ')
        summary = entry.find('atom:summary', ns).text.strip().replace('\n', ' ')
        paper_id = entry.find('atom:id', ns).text.strip()
        published = entry.find('atom:published', ns).text.strip()[:10]
        
        authors = [a.find('atom:name', ns).text for a in entry.findall('atom:author', ns)]
        
        papers.append({
            'id': paper_id,
            'title': title,
            'summary': summary,
            'published': published,
            'authors': ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        })
    return papers

def extract_metrics_with_gemini(paper, api_key):
    """Use Gemini API to check if paper has new M-R measurements and parse them."""
    client = genai.Client(api_key=api_key)
    
    prompt = f"""
    Analyze this astrophysics preprint abstract to check if it reports NEW NICER pulse profile modeling or Mass-Radius measurements for any of these 7 core pulsars:
    - PSR J0030+0451
    - PSR J0740+6620
    - PSR J0437-4715
    - PSR J0614-3329
    - PSR J1231-1411
    - PSR J2124-3358
    - PSR J1614-2230

    Paper Title: {paper['title']}
    Authors: {paper['authors']}
    Abstract: {paper['summary']}

    If this paper presents NEW empirical mass/radius constraints, return a JSON object with this exact structure:
    {{
      "is_nicer_mr_analysis": true,
      "pulsar_id": "j0030" (matching one of: j0030, j0740, j0437, j0614, j1231, j2124, j1614),
      "analysis": {{
        "author": "{paper['authors'].split(',')[0]} ({paper['published'][:4]})",
        "group": "X-PSI or IM or Independent",
        "mass": "1.40 ± 0.12",
        "radius": "11.71 ± 0.85",
        "status": "ACTIVE",
        "supersededBy": null,
        "keyFeature": "Brief summary of key method or finding (max 15 words)"
      }},
      "supersedes_previous": true
    }}

    If it does NOT contain new M-R estimates for these 7 pulsars, respond ONLY with:
    {{"is_nicer_mr_analysis": false}}
    """
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config={'response_mime_type': 'application/json'}
    )
    
    try:
        return json.loads(response.text)
    except Exception as e:
        print(f"Error parsing JSON output from Gemini: {e}")
        return {"is_nicer_mr_analysis": False}

def update_database():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable not set.")
        return

    # Load existing database
    if os.path.exists(DATA_FILE_PATH):
        with open(DATA_FILE_PATH, 'r') as f:
            database = json.load(f)
    else:
        print(f"Database file {DATA_FILE_PATH} not found.")
        return

    papers = fetch_recent_arxiv_papers()
    updated = False

    for paper in papers:
        extracted = extract_metrics_with_gemini(paper, api_key)
        
        if extracted.get("is_nicer_mr_analysis"):
            pulsar_id = extracted.get("pulsar_id")
            new_analysis = extracted.get("analysis")
            
            # Find target pulsar in database
            target_pulsar = next((p for p in database if p['id'] == pulsar_id), None)
            if target_pulsar:
                # Check if analysis already exists to prevent duplicate entries
                exists = any(a['author'] == new_analysis['author'] for a in target_pulsar['analyses'])
                if not exists:
                    print(f"Adding new analysis for {target_pulsar['name']}: {new_analysis['author']}")
                    
                    if extracted.get("supersedes_previous"):
                        for old_a in target_pulsar['analyses']:
                            if old_a['status'] == 'ACTIVE':
                                old_a['status'] = 'SUPERSEDED'
                                old_a['supersededBy'] = new_analysis['author']
                    
                    target_pulsar['analyses'].insert(0, new_analysis)
                    updated = True

    if updated:
        with open(DATA_FILE_PATH, 'w') as f:
            json.dump(database, f, indent=4)
        print("Database successfully updated and saved!")
    else:
        print("No new mass-radius analyses detected today.")

if __name__ == "__main__":
    update_database()
