from pathlib import Path
import re

ass = Path(r"C:\Users\szabo\.verticals\media\work_1788467146_en\captions_en.ass")
lines = ass.read_text(encoding="utf-8").splitlines()

targets = {"0:01:10.87,0:01:11.15", "0:01:11.15,0:01:11.37", "0:01:11.37,0:01:11.64"}
out = []
for line in lines:
    if any(t in line for t in targets) and "GAMING" in line and " IN " in line:
        line = line.replace(" IN ", " AND ").replace("}IN{", "}AND{")
    out.append(line)

ass.write_text("\n".join(out) + "\n", encoding="utf-8")
print("patched")
