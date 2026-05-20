from PyInstaller.utils.hooks import get_module_file_attribute
import os

datas = []

# Inclure les métadonnées de Streamlit
try:
    streamlit_path = get_module_file_attribute("streamlit").parent
    streamlit_dist_info = streamlit_path.parent / "streamlit-*.dist-info"
    
    # Chercher le dossier dist-info
    import glob
    dist_infos = glob.glob(str(streamlit_dist_info))
    if dist_infos:
        datas.append((dist_infos[0], "streamlit-1.36.0.dist-info"))
except Exception:
    pass

hiddenimports = [
    "streamlit.runtime",
    "streamlit.components",
]
