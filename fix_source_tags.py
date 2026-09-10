import sqlite3

conn = sqlite3.connect(r"D:\verticals_media_library\index.db")
paths = [
    r"D:\verticals_media_library\photos\7b8edfc441ea4619ba0546c4.jpg",
    r"D:\verticals_media_library\photos\24336aa0e897e0e65584d3c2.jpg",
    r"D:\verticals_media_library\photos\0364bdce5c8ab5bd6d97af69.jpg",
    r"D:\verticals_media_library\photos\7f4f0ebe9f7830bcba981640.jpg",
    r"D:\verticals_media_library\photos\b013a4ee57a17a62c5ef68da.jpg",
]
for p in paths:
    cur = conn.execute("UPDATE assets SET source=? WHERE local_path=?", ("wikimedia_official_art", p))
    print(p, "rows updated:", cur.rowcount)
conn.commit()
