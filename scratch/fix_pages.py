import os

files = [
    r"pages\aujourdhui.py",
    r"pages\achievements.py",
    r"pages\aide.py",
    r"pages\objectifs.py",
    r"pages\revisions.py"
]

for f in files:
    with open(f, "a", encoding="utf-8") as file:
        file.write('\n\nif __name__ == "__main__":\n    render()\n')
print("Terminé")
