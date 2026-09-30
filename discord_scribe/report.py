"""Self-contained, escaped HTML report; no server, CDN, or external scripts."""

from html import escape
from pathlib import Path

from .service import Service, atomic_write


def write_report(service: Service, path: Path) -> None:
    if path.resolve() in {service.config.database.resolve(), service.config.export.resolve()}:
        raise ValueError("Report path must differ from the database and Markdown export")
    rows = service.store.records()
    cards = []
    for row in rows:
        url = f"https://discord.com/channels/{row['guild_id']}/{row['channel_id']}/{row['message_id']}"
        cards.append(f"""<article data-status="{row['status']}">
<div class="eyebrow">{escape(row['category'])} <span class="badge">{row['status']}</span></div>
<h2>{escape(row['summary'])}</h2><blockquote>{escape(row['evidence'])}</blockquote>
<div class="meta">Context #{row['id']} · Author {row['author_id']} · {escape(row['updated_at'])}</div>
<details><summary>Inspect full source and extraction</summary><p class="source">{escape(row['content'])}</p>
<p>Model: {escape(row['model'])}<br>Self-reported confidence: {row['confidence']:.0%} (not calibrated)</p>
<p>Review locally: <code>python -m discord_scribe approve {row['id']}</code> or <code>python -m discord_scribe reject {row['id']}</code></p></details>
<a href="{url}" target="_blank" rel="noreferrer">Open source message ↗</a></article>""")
    approved = sum(row["status"] == "approved" for row in rows)
    pending = sum(row["status"] == "pending" for row in rows)
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Discord Scribe · Context review</title>
<style>
:root{color-scheme:dark;font-family:system-ui,sans-serif;background:#0c1119;color:#edf1f7}
*{box-sizing:border-box}body{margin:0}main{max-width:1100px;margin:auto;padding:48px 24px}
header{border-bottom:1px solid #283344;padding-bottom:32px}.wordmark{font-size:14px;letter-spacing:.16em;color:#79dfcb;font-weight:700}
h1{font-size:clamp(32px,6vw,58px);letter-spacing:-.045em;margin:22px 0 14px;line-height:1.1}
.intro{max-width:660px;color:#aebdce;font-size:18px;line-height:1.7}.stats{display:flex;gap:40px;margin:28px 0 8px}.stats strong{display:block;font-size:28px;color:#fff}.stats span{color:#aebdce;font-size:13px}
.controls{display:flex;gap:16px;flex-wrap:wrap;align-items:center;padding:28px 0}input,select{font:inherit;color:inherit;background:#141e2b;border:1px solid #435166;border-radius:8px;padding:12px}input{width:min(100%,400px)}
:focus-visible{outline:3px solid #79dfcb;outline-offset:4px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:18px}
article{background:#121b27;border:1px solid #2c3a4d;border-radius:14px;padding:26px}article[hidden]{display:none}.eyebrow{text-transform:uppercase;letter-spacing:.11em;font-size:11px;color:#79dfcb;display:flex;justify-content:space-between;gap:12px}.badge{color:#cbd5e1;background:#263447;padding:3px 8px;border-radius:10px}
h2{font-size:19px;line-height:1.5;font-weight:600}blockquote{margin:20px 0;padding-left:16px;border-left:2px solid #577088;color:#b7c6d8;line-height:1.65;white-space:pre-wrap;overflow-wrap:anywhere}.meta{font-size:11px;color:#aebdce;overflow-wrap:anywhere}a{color:#79dfcb;font-size:13px;text-underline-offset:4px}details{font-size:13px;line-height:1.6;margin:20px 0;color:#c1cede}summary{cursor:pointer}.source{white-space:pre-wrap}code{overflow-wrap:anywhere}footer{color:#9cacc1;font-size:12px;line-height:1.7;margin-top:36px}
</style></head><body><main><header><div class="wordmark">DISCORD SCRIBE / PROJECT MEMORY</div>
<h1>Good context.<br>Clear provenance.</h1><p class="intro">PROJECT · A reviewable record of what your team decided, proposed, and needs to do. Every statement leads back to its source.</p>
<div class="stats"><div><strong>TOTAL</strong><span>context items</span></div><div><strong>APPROVED</strong><span>approved</span></div><div><strong>PENDING</strong><span>awaiting review</span></div></div></header>
<div class="controls"><label for="search">Search</label><input id="search" type="search" placeholder="Find a decision, constraint, or source…"><label for="status">Status</label><select id="status"><option value="">All items</option><option>pending</option><option>approved</option><option>rejected</option></select></div>
<p id="count" aria-live="polite"></p><section class="grid" aria-label="Context items">CARDS</section>
<footer>This is a local snapshot. Regenerate it after review; it does not update live. Synthetic demo reports auto-approve only fixture data. Live records require an explicit review command. Model confidence is not proof of correctness. Private report copies are not removed when the source message is deleted.</footer>
</main><script>
const search=document.querySelector('#search'), status=document.querySelector('#status');
function filter(){let count=0;document.querySelectorAll('article').forEach(card=>{card.hidden=!(card.textContent.toLowerCase().includes(search.value.toLowerCase())&&(!status.value||card.dataset.status===status.value));if(!card.hidden)count++});document.querySelector('#count').textContent=count+' items shown'}search.addEventListener('input',filter);status.addEventListener('change',filter);filter();
</script></body></html>"""
    # Replace markers before inserting untrusted strings, so content cannot become a marker.
    html = html.replace("TOTAL", str(len(rows))).replace("APPROVED", str(approved)).replace("PENDING", str(pending))
    html = html.replace("PROJECT ·", escape(service.config.project) + " ·").replace("CARDS", "".join(cards))
    atomic_write(path.resolve(), html)
