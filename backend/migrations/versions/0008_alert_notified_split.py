"""Distingue la notification d'OUVERTURE de celle de FERMETURE.

`notified_at` (migration 0007) était écrit par `notify_alert` et jamais relu : il ne
servait ni de garde, ni de rattrapage. Or Celery est configuré en at-least-once
(`task_acks_late=True`, `app/celery_app.py`) : un worker tué après l'envoi mais avant
l'acquittement REJOUE la tâche, ce qui renvoyait la même notification une deuxième fois.

Une seule colonne ne peut pas servir de garde contre ce rejeu : une alerte est notifiée
DEUX FOIS légitimement dans sa vie -- à son ouverture, puis à sa fermeture. Un seul
timestamp ne peut pas distinguer "l'ouverture a déjà été notifiée" de "la fermeture a
déjà été notifiée". D'où deux colonnes distinctes, chacune la garde de SON événement.

Revision ID: 0008_alert_notified_split
"""
import sqlalchemy as sa
from alembic import op

revision = "0008_alert_notified_split"
down_revision = "0007_alert"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alert", sa.Column("opened_notified_at", sa.DateTime(timezone=True)))
    op.add_column("alert", sa.Column("closed_notified_at", sa.DateTime(timezone=True)))
    op.drop_column("alert", "notified_at")


def downgrade() -> None:
    op.add_column("alert", sa.Column("notified_at", sa.DateTime(timezone=True)))
    op.drop_column("alert", "closed_notified_at")
    op.drop_column("alert", "opened_notified_at")
