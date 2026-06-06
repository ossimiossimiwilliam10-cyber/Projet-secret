"""Launcher pour éviter les problèmes Streamlit + PyInstaller"""
import sys
import os

# Patch la détection de version Streamlit avant l'import
import importlib.metadata
original_version = importlib.metadata.version

def patched_version(package):
    if package == "streamlit":
        return "1.36.0"
    return original_version(package)

importlib.metadata.version = patched_version

# Import et lance l'app
if __name__ == "__main__":
    import streamlit.web.cli as stcli
    
    # Cherche app.py dans le répertoire courant ou parent
    app_path = "app.py"
    if not os.path.exists(app_path) and os.path.exists("../app.py"):
        app_path = "../app.py"
    
    sys.argv = ["streamlit", "run", app_path, "--logger.level=warning", "--client.showErrorDetails=false"]
    stcli.main()
