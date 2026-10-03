"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SEQUENCES = ("contract_no_seq", "application_ref_seq", "payment_ref_seq")


def upgrade() -> None:
    for name in SEQUENCES:
        op.execute(sa.schema.CreateSequence(sa.Sequence(name)))
    op.create_table('loan_products',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('code', sa.String(length=30), nullable=False),
    sa.Column('type', sa.Enum('consumer', 'car', 'mortgage', 'business', name='loantype', native_enum=False, length=20), nullable=False),
    sa.Column('name_key', sa.String(length=60), nullable=False),
    sa.Column('loan_name_key', sa.String(length=60), nullable=False),
    sa.Column('annual_rate', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('min_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('max_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('step', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('min_term', sa.Integer(), nullable=False),
    sa.Column('max_term', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('otp_codes',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('phone', sa.String(length=20), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_otp_codes_phone'), 'otp_codes', ['phone'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('phone', sa.String(length=20), nullable=False),
    sa.Column('full_name', sa.String(length=120), nullable=False),
    sa.Column('language', sa.String(length=5), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_phone'), 'users', ['phone'], unique=True)
    op.create_table('loan_applications',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('reference', sa.String(length=20), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('type', sa.Enum('consumer', 'car', 'mortgage', 'business', name='loantype', native_enum=False, length=20), nullable=False),
    sa.Column('product_name_key', sa.String(length=60), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('term_months', sa.Integer(), nullable=False),
    sa.Column('annual_rate', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('monthly_payment', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('purpose', sa.String(length=40), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('status', sa.Enum('submitted', 'approved', 'rejected', 'cancelled', name='applicationstatus', native_enum=False, length=20), nullable=False),
    sa.Column('decision_note', sa.String(length=500), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['product_id'], ['loan_products.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('reference')
    )
    op.create_index(op.f('ix_loan_applications_status'), 'loan_applications', ['status'], unique=False)
    op.create_index(op.f('ix_loan_applications_user_id'), 'loan_applications', ['user_id'], unique=False)
    op.create_table('refresh_tokens',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('device_name', sa.String(length=120), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('replaced_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_refresh_tokens_user_id'), 'refresh_tokens', ['user_id'], unique=False)
    op.create_table('loans',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=True),
    sa.Column('type', sa.Enum('consumer', 'car', 'mortgage', 'business', name='loantype', native_enum=False, length=20), nullable=False),
    sa.Column('product_name_key', sa.String(length=60), nullable=False),
    sa.Column('contract_no', sa.String(length=30), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('annual_rate', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('term_months', sa.Integer(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['loan_applications.id'], ),
    sa.ForeignKeyConstraint(['product_id'], ['loan_products.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('application_id'),
    sa.UniqueConstraint('contract_no')
    )
    op.create_index(op.f('ix_loans_user_id'), 'loans', ['user_id'], unique=False)
    op.create_table('documents',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('loan_id', sa.Uuid(), nullable=False),
    sa.Column('name_key', sa.String(length=40), nullable=False),
    sa.Column('file_name', sa.String(length=120), nullable=False),
    sa.Column('content_type', sa.String(length=60), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('content', sa.LargeBinary(), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['loan_id'], ['loans.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_documents_loan_id'), 'documents', ['loan_id'], unique=False)
    op.create_table('installments',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('loan_id', sa.Uuid(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('due_date', sa.Date(), nullable=False),
    sa.Column('principal', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('interest', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('balance_after', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['loan_id'], ['loans.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('loan_id', 'number')
    )
    op.create_index(op.f('ix_installments_loan_id'), 'installments', ['loan_id'], unique=False)
    op.create_table('payments',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('reference', sa.String(length=30), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('loan_id', sa.Uuid(), nullable=False),
    sa.Column('installment_id', sa.BigInteger(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('idempotency_key', sa.String(length=80), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['installment_id'], ['installments.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['loan_id'], ['loans.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('installment_id'),
    sa.UniqueConstraint('reference'),
    sa.UniqueConstraint('user_id', 'idempotency_key')
    )
    op.create_index(op.f('ix_payments_loan_id'), 'payments', ['loan_id'], unique=False)
    op.create_index(op.f('ix_payments_user_id'), 'payments', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_payments_user_id'), table_name='payments')
    op.drop_index(op.f('ix_payments_loan_id'), table_name='payments')
    op.drop_table('payments')
    op.drop_index(op.f('ix_installments_loan_id'), table_name='installments')
    op.drop_table('installments')
    op.drop_index(op.f('ix_documents_loan_id'), table_name='documents')
    op.drop_table('documents')
    op.drop_index(op.f('ix_loans_user_id'), table_name='loans')
    op.drop_table('loans')
    op.drop_index(op.f('ix_refresh_tokens_user_id'), table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
    op.drop_index(op.f('ix_loan_applications_user_id'), table_name='loan_applications')
    op.drop_index(op.f('ix_loan_applications_status'), table_name='loan_applications')
    op.drop_table('loan_applications')
    op.drop_index(op.f('ix_users_phone'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_otp_codes_phone'), table_name='otp_codes')
    op.drop_table('otp_codes')
    op.drop_table('loan_products')
    for name in SEQUENCES:
        op.execute(sa.schema.DropSequence(sa.Sequence(name)))
