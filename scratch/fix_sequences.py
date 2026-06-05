import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from database.db import engine

def fix_postgresql_sequences():
    load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url or not db_url.startswith("postgresql"):
        print("DATABASE_URL n'est pas configuré pour PostgreSQL. Aucune action requise.")
        return

    inspector = inspect(engine)
    tables = inspector.get_table_names()

    with engine.begin() as conn:
        for table in tables:
            try:
                # Dans PostgreSQL, les séquences créées via SERIAL s'appellent table_id_seq
                seq_name = f"{table}_id_seq"
                
                # Récupérer l'ID max actuel
                res = conn.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {table}"))
                max_id = res.scalar()
                
                if max_id > 0:
                    # Mettre à jour la séquence
                    conn.execute(text(f"SELECT setval('{seq_name}', {max_id})"))
                    print(f"Séquence {seq_name} mise à jour à {max_id}")
            except Exception as e:
                # Si la table n'a pas de colonne id ou pas de séquence
                pass
    
    print("Mise à jour des séquences terminée avec succès.")

if __name__ == "__main__":
    fix_postgresql_sequences()
