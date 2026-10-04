"""admin panel: admin users, audit log, payment method and reversal

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04

Admin panel accounts (app_admin_users), audit log (app_audit_log) and, on
app_payments: method, note, who recorded it and reversal fields. The unique
installment_id constraint becomes a partial unique index on live payments, so a
reversed payment frees its installment.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('app_admin_users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('username', sa.String(length=60), nullable=False),
    sa.Column('full_name', sa.String(length=120), nullable=False),
    sa.Column('role', sa.Enum('admin', 'operator', 'viewer', name='adminrole', native_enum=False, length=20), nullable=False),
    sa.Column('password_hash', sa.String(length=200), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('must_change_password', sa.Boolean(), nullable=False),
    sa.Column('failed_logins', sa.Integer(), nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('token_version', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_app_admin_users_username'), 'app_admin_users', ['username'], unique=True)
    op.create_table('app_audit_log',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('admin_id', sa.Uuid(), nullable=True),
    sa.Column('admin_username', sa.String(length=60), nullable=False),
    sa.Column('action', sa.String(length=60), nullable=False),
    sa.Column('entity', sa.String(length=40), nullable=False),
    sa.Column('entity_id', sa.String(length=64), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['admin_id'], ['app_admin_users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_app_audit_log_action'), 'app_audit_log', ['action'], unique=False)
    op.create_index(op.f('ix_app_audit_log_admin_id'), 'app_audit_log', ['admin_id'], unique=False)
    op.create_index(op.f('ix_app_audit_log_created_at'), 'app_audit_log', ['created_at'], unique=False)
    op.create_index(op.f('ix_app_audit_log_entity_id'), 'app_audit_log', ['entity_id'], unique=False)
    op.add_column('app_payments', sa.Column('method', sa.Enum('app', 'cash', 'bank_transfer', 'card', name='paymentmethod', native_enum=False, length=20), server_default='app', nullable=False))
    op.add_column('app_payments', sa.Column('note', sa.String(length=500), nullable=True))
    op.add_column('app_payments', sa.Column('created_by_admin_id', sa.Uuid(), nullable=True))
    op.add_column('app_payments', sa.Column('reversed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('app_payments', sa.Column('reversed_by_admin_id', sa.Uuid(), nullable=True))
    op.add_column('app_payments', sa.Column('reversal_reason', sa.String(length=500), nullable=True))
    op.drop_constraint(op.f('app_payments_installment_id_key'), 'app_payments', type_='unique')
    op.create_index(op.f('ix_app_payments_installment_id'), 'app_payments', ['installment_id'], unique=False)
    op.create_index('uq_app_payments_installment_live', 'app_payments', ['installment_id'], unique=True, postgresql_where=sa.text('reversed_at IS NULL'))
    op.create_foreign_key('fk_app_payments_created_by_admin', 'app_payments', 'app_admin_users', ['created_by_admin_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_app_payments_reversed_by_admin', 'app_payments', 'app_admin_users', ['reversed_by_admin_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    # The old schema allows one payment per installment: drop reversed ones first.
    op.execute("DELETE FROM app_payments WHERE reversed_at IS NOT NULL")
    op.drop_constraint('fk_app_payments_reversed_by_admin', 'app_payments', type_='foreignkey')
    op.drop_constraint('fk_app_payments_created_by_admin', 'app_payments', type_='foreignkey')
    op.drop_index('uq_app_payments_installment_live', table_name='app_payments', postgresql_where=sa.text('reversed_at IS NULL'))
    op.drop_index(op.f('ix_app_payments_installment_id'), table_name='app_payments')
    op.create_unique_constraint(op.f('app_payments_installment_id_key'), 'app_payments', ['installment_id'])
    op.drop_column('app_payments', 'reversal_reason')
    op.drop_column('app_payments', 'reversed_by_admin_id')
    op.drop_column('app_payments', 'reversed_at')
    op.drop_column('app_payments', 'created_by_admin_id')
    op.drop_column('app_payments', 'note')
    op.drop_column('app_payments', 'method')
    op.drop_index(op.f('ix_app_audit_log_entity_id'), table_name='app_audit_log')
    op.drop_index(op.f('ix_app_audit_log_created_at'), table_name='app_audit_log')
    op.drop_index(op.f('ix_app_audit_log_admin_id'), table_name='app_audit_log')
    op.drop_index(op.f('ix_app_audit_log_action'), table_name='app_audit_log')
    op.drop_table('app_audit_log')
    op.drop_index(op.f('ix_app_admin_users_username'), table_name='app_admin_users')
    op.drop_table('app_admin_users')
