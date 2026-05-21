from flask import Blueprint, render_template
from database.db import get_session
from services.revision_service import chapitres_a_reviser

overview_bp = Blueprint('overview', __name__, url_prefix='/overview')

@overview_bp.route('/')
def overview():
    session = get_session()
    chapitres = chapitres_a_reviser(session)
    session.close()
    return render_template('overview.html', chapitres=chapitres)
