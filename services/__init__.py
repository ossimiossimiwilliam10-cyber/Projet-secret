"""Package ``services`` — logiques métier (analyse PDF, planification IA, exports).

Les imports sont volontairement limités à ``pdf_analyzer`` pour éviter
de charger tout le graphe de dépendances au démarrage (google-genai,
pdfplumber, PyMuPDF, etc.). Les autres services sont importés à la
demande par les modules qui en ont besoin.
"""

