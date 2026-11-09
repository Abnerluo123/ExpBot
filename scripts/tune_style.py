"""Development-only readability adjustment, applied once by the author."""
from pathlib import Path
import re
p = Path(__file__).resolve().parents[1] / 'app/web/style.css'
s = p.read_text(encoding='utf-8')
mapping = {7:10, 8:10, 9:11, 10:12, 11:13, 12:13, 13:14}
s = re.sub(r'font-size:(7|8|9|10|11|12|13)px', lambda m: 'font-size:' + str(mapping[int(m.group(1))]) + 'px', s)
s += '\n/* Reading comfort for document-heavy workflows. */\n.drop-copy span{color:#978eac}.drop-copy small{color:#a39ab5}.audit-line strong{color:#81798f}.audit-line small{color:#a09aae}.amount-row{color:#a29bac}.amount-row strong{color:#8b7d9b}.panel-heading>div>span{color:#a89db5}.school-badge span{color:#a191b5}.audit-footer p{color:#aa9fb8}.filter-tabs button{color:#948b9f}.materials-tip{color:#a69caf}.bottom-note{color:#aa9eb6}.empty-materials h3{color:#a092b0}.empty-materials p{color:#b4a8c2}.sidebar .order-row small{font-size:10px}.sidebar .privacy-note span{font-size:10px}\n'
p.write_text(s, encoding='utf-8')
print('Readability styles applied')
