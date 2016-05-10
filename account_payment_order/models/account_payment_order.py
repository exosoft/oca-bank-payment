# -*- coding: utf-8 -*-
# © 2009 EduSense BV (<http://www.edusense.nl>)
# © 2011-2013 Therp BV (<http://therp.nl>)
# © 2016 Serv. Tecnol. Avanzados - Pedro M. Baeza
# © 2016 Akretion (Alexis de Lattre - alexis.delattre@akretion.com)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from openerp import models, fields, api, _
from openerp.exceptions import UserError, ValidationError


class AccountPaymentOrder(models.Model):
    _name = 'account.payment.order'
    _description = 'Payment Order'
    _inherit = ['mail.thread']
    _order = 'id desc'

    name = fields.Char(
        string='Number', readonly=True, copy=False)  # v8 field : name
    payment_mode_id = fields.Many2one(
        'account.payment.mode', 'Payment Method', required=True,
        ondelete='restrict', track_visibility='onchange',
        readonly=True, states={'draft': [('readonly', False)]})
    payment_type = fields.Selection([
        ('inbound', 'Inbound'),
        ('outbound', 'Outbound'),
        ], string='Payment Type', readonly=True, required=True)
    company_id = fields.Many2one(
        related='payment_mode_id.company_id', store=True, readonly=True)
    company_currency_id = fields.Many2one(
        related='payment_mode_id.company_id.currency_id', store=True,
        readonly=True)
    bank_account_link = fields.Selection(
        related='payment_mode_id.bank_account_link', readonly=True)
    journal_id = fields.Many2one(
        'account.journal', string='Bank Journal', ondelete='restrict',
        readonly=True, states={'draft': [('readonly', False)]},
        track_visibility='onchange')
    allowed_journal_ids = fields.Many2many(
        'account.journal', compute='_compute_allowed_journals', readonly=True,
        string='Selectable Bank Journals')
    # The journal_id field is only required at confirm step, to
    # allow auto-creation of payment order from invoice
    company_partner_bank_id = fields.Many2one(
        related='journal_id.bank_account_id', string='Company Bank Account',
        readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('open', 'Confirmed'),
        ('generated', 'File Generated'),
        ('uploaded', 'File Uploaded'),
        ('cancel', 'Cancel'),
        ], string='Status', readonly=True, copy=False, default='draft',
        track_visibility='onchange')
    date_prefered = fields.Selection([
        ('now', 'Immediately'),
        ('due', 'Due Date'),
        ('fixed', 'Fixed Date'),
        ], string='Payment Execution Date Type', required=True, default='due',
        track_visibility='onchange', readonly=True,
        states={'draft': [('readonly', False)]})
    date_scheduled = fields.Date(
        string='Payment Execution Date', readonly=True,
        states={'draft': [('readonly', False)]}, track_visibility='onchange',
        help="Select a requested date of execution if you selected 'Due Date' "
        "as the Payment Execution Date Type.")
    date_generated = fields.Date(string='File Generation Date', readonly=True)
    date_uploaded = fields.Date(string='File Upload Date', readonly=True)
    generated_user_id = fields.Many2one(
        'res.users', string='Generated by', readonly=True, ondelete='restrict',
        copy=False)
    payment_line_ids = fields.One2many(
        'account.payment.line', 'order_id', string='Transaction Lines',
        readonly=True, states={'draft': [('readonly', False)]})
    # v8 field : line_ids
    bank_line_ids = fields.One2many(
        'bank.payment.line', 'order_id', string="Bank Payment Lines",
        readonly=True)
    total_company_currency = fields.Monetary(
        compute='_compute_total', store=True, readonly=True,
        currency_field='company_currency_id')
    bank_line_count = fields.Integer(
        compute='_bank_line_count', string='Number of Bank Lines',
        readonly=True)
    move_ids = fields.One2many(
        'account.move', 'payment_order_id', string='Transfer Journal Entries',
        readonly=True)

    @api.multi
    @api.constrains('payment_type', 'payment_mode_id')
    def payment_order_constraints(self):
        for order in self:
            if (
                    order.payment_mode_id.payment_type and
                    order.payment_mode_id.payment_type != order.payment_type):
                raise ValidationError(_(
                    "The payment type (%s) is not the same as the payment "
                    "type of the payment mode (%s)") % (
                        order.payment_type,
                        order.payment_mode_id.payment_type))

    @api.multi
    @api.constrains('date_scheduled')
    def check_date_scheduled(self):
        today = fields.Date.context_today(self)
        for order in self:
            if order.date_scheduled:
                if order.date_scheduled < today:
                    raise ValidationError(_(
                        "On payment order %s, the Payment Execution Date "
                        "is in the past (%s).")
                        % (order.name, order.date_scheduled))

    @api.one
    @api.depends(
        'payment_line_ids', 'payment_line_ids.amount_company_currency')
    def _compute_total(self):
        self.total_company_currency = sum(
            self.mapped('payment_line_ids.amount_company_currency') or [0.0])

    @api.multi
    @api.depends('bank_line_ids')
    def _bank_line_count(self):
        for order in self:
            order.bank_line_count = len(order.bank_line_ids)

    @api.multi
    @api.depends('payment_mode_id')
    def _compute_allowed_journals(self):
        for order in self:
            allowed_journal_ids = False
            if order.payment_mode_id:
                mode = order.payment_mode_id
                if mode.bank_account_link == 'fixed':
                    allowed_journal_ids = mode.fixed_journal_id
                else:
                    allowed_journal_ids = mode.variable_journal_ids
            order.allowed_journal_ids = allowed_journal_ids

    @api.model
    def create(self, vals):
        if vals.get('name', 'New') == 'New':
            vals['name'] = self.env['ir.sequence'].next_by_code(
                'account.payment.order') or 'New'
        if vals.get('payment_mode_id'):
            payment_mode = self.env['account.payment.mode'].browse(
                vals['payment_mode_id'])
            vals['payment_type'] = payment_mode.payment_type
            if payment_mode.bank_account_link == 'fixed':
                vals['journal_id'] = payment_mode.fixed_journal_id.id
        return super(AccountPaymentOrder, self).create(vals)

    @api.onchange('payment_mode_id')
    def payment_mode_id_change(self):
        journal_id = False
        if self.payment_mode_id:
            if self.payment_mode_id.bank_account_link == 'fixed':
                journal_id = self.payment_mode_id.fixed_journal_id
        self.journal_id = journal_id

    @api.multi
    def action_done(self):
        self.write({
            'date_done': fields.Date.context_today(self),
            'state': 'done',
            })
        return True

    @api.multi
    def cancel2draft(self):
        self.write({'state': 'draft'})
        return True

    @api.multi
    def action_cancel(self):
        for order in self:
            order.write({'state': 'cancel'})
            order.bank_line_ids.unlink()
        return True

    @api.model
    def _prepare_bank_payment_line(self, paylines):
        return {
            'order_id': paylines[0].order_id.id,
            'payment_line_ids': [(6, 0, paylines.ids)],
            'communication': '-'.join(
                [line.communication for line in paylines]),
            }

    @api.multi
    def draft2open(self):
        """
        Called when you click on the 'Confirm' button
        Set the 'date' on payment line depending on the 'date_prefered'
        setting of the payment.order
        Re-generate the bank payment lines
        """
        bplo = self.env['bank.payment.line']
        today = fields.Date.context_today(self)
        for order in self:
            if not order.journal_id:
                raise UserError(_(
                    'Missing Bank Journal on payment order %s.') % order.name)
            if not order.payment_line_ids:
                raise UserError(_(
                    'There are no transactions on payment order %s.')
                    % order.name)
            # Delete existing bank payment lines
            order.bank_line_ids.unlink()
            # Create the bank payment lines from the payment lines
            group_paylines = {}  # key = hashcode
            for payline in order.payment_line_ids:
                payline.check_payment_line()
                # Compute requested payment date
                if order.date_prefered == 'due':
                    requested_date = payline.ml_maturity_date or today
                elif order.date_prefered == 'fixed':
                    requested_date = order.date_scheduled or today
                else:
                    requested_date = today
                # No payment date in the past
                if requested_date < today:
                    requested_date = today
                # Write requested_date on 'date' field of payment line
                payline.date = requested_date
                # Group options
                if order.payment_mode_id.group_lines:
                    hashcode = payline.payment_line_hashcode()
                else:
                    # Use line ID as hascode, which actually means no grouping
                    hashcode = payline.id
                if hashcode in group_paylines:
                    group_paylines[hashcode]['paylines'] += payline
                    group_paylines[hashcode]['total'] +=\
                        payline.amount_currency
                else:
                    group_paylines[hashcode] = {
                        'paylines': payline,
                        'total': payline.amount_currency,
                    }
            # Create bank payment lines
            for paydict in group_paylines.values():
                # Block if a bank payment line is <= 0
                if paydict['total'] <= 0:
                    raise UserError(_(
                        "The amount for Partner '%s' is negative "
                        "or null (%.2f) !")
                        % (paydict['paylines'][0].partner_id.name,
                           paydict['total']))
                vals = self._prepare_bank_payment_line(paydict['paylines'])
                bplo.create(vals)
        self.write({'state': 'open'})
        return True

    @api.multi
    def generate_payment_file(self):
        """Returns (payment file as string, filename)"""
        self.ensure_one()
        raise UserError(_(
            "No handler for this payment method. Maybe you haven't "
            "installed the related Odoo module."))

    @api.multi
    def open2generated(self):
        self.ensure_one()
        payment_file_str, filename = self.generate_payment_file()
        attachment = self.env['ir.attachment'].create({
            'res_model': 'account.payment.order',
            'res_id': self.id,
            'name': filename,
            'datas': payment_file_str.encode('base64'),
            'datas_fname': filename,
            })
        self.write({
            'date_generated': fields.Date.context_today(self),
            'state': 'generated',
            'generated_user_id': self._uid,
            })
        simplified_form_view = self.env.ref(
            'account_payment_order.view_attachment_simplified_form')
        action = {
            'name': _('Payment File'),
            'view_mode': 'form',
            'view_id': simplified_form_view.id,
            'res_model': 'ir.attachment',
            'type': 'ir.actions.act_window',
            'target': 'current',
            'res_id': attachment.id,
            }
        return action

    @api.multi
    def generated2uploaded(self):
        for order in self:
            if order.payment_mode_id.transfer_move:
                order.generate_transfer_move()
        self.write({
            'state': 'uploaded',
            'date_uploaded': fields.Date.context_today(self),
            })
        return True

    # Generation of transfer move
    @api.multi
    def _prepare_transfer_move(self):
        if self.payment_type == 'outbound':
            ref = _('Payment order %s') % self.name
        else:
            ref = _('Debit order %s') % self.name
        vals = {
            'journal_id': self.payment_mode_id.transfer_journal_id.id,
            'ref': ref,
            'payment_order_id': self.id,
            'line_ids': [],
            }
        return vals

    @api.multi
    def _prepare_move_line_transfer_account(
            self, amount, bank_payment_lines):
        if self.payment_type == 'outbound':
            name = _('Payment order %s') % self.name
        else:
            name = _('Debit order %s') % self.name
        date_maturity = bank_payment_lines[0].date
        vals = {
            'name': name,
            'partner_id': False,
            'account_id': self.payment_mode_id.transfer_account_id.id,
            'credit': (self.payment_type == 'outbound' and
                       amount or 0.0),
            'debit': (self.payment_type == 'inbound' and
                      amount or 0.0),
            'date_maturity': date_maturity,
        }
        return vals

    @api.multi
    def _prepare_move_line_partner_account(self, bank_line):
        # TODO : ALEXIS check don't group if move_line_id.account_id
        # is not the same
        if bank_line.payment_line_ids[0].move_line_id:
            account_id =\
                bank_line.payment_line_ids[0].move_line_id.account_id.id
        else:
            if self.payment_type == 'inbound':
                account_id =\
                    bank_line.partner_id.property_account_receivable_id.id
            else:
                account_id =\
                    bank_line.partner_id.property_account_payable_id.id
        if self.payment_type == 'outbound':
            name = _('Payment bank line %s') % bank_line.name
        else:
            name = _('Debit bank line %s') % bank_line.name
        vals = {
            'name': name,
            'bank_payment_line_id': bank_line.id,
            'partner_id': bank_line.partner_id.id,
            'account_id': account_id,
            'credit': (self.payment_type == 'inbound' and
                       bank_line.amount_currency or 0.0),
            'debit': (self.payment_type == 'outbound' and
                      bank_line.amount_currency or 0.0),
            }
        return vals

    @api.multi
    def generate_transfer_move(self):
        """
        Create the moves that pay off the move lines from
        the payment/debit order.
        """
        self.ensure_one()
        am_obj = self.env['account.move']
        # prepare a dict "trfmoves" that can be used when
        # self.payment_mode_id.transfer_move_option = date or line
        # key = unique identifier (date or True or line.id)
        # value = bank_pay_lines (recordset that can have several entries)
        trfmoves = {}
        for bline in self.bank_line_ids:
            hashcode = bline.move_line_transfer_account_hashcode()
            if hashcode in trfmoves:
                trfmoves[hashcode] += bline
            else:
                trfmoves[hashcode] = bline

        company_currency = self.env.user.company_id.currency_id
        for hashcode, blines in trfmoves.iteritems():
            mvals = self._prepare_transfer_move()
            total_amount = 0
            for bline in blines:
                total_amount += bline.amount_currency
                if bline.currency_id != company_currency:
                    raise UserError(_(
                        "Cannot generate the transfer move when "
                        "the currency of the payment (%s) is not the "
                        "same as the currency of the company (%s). This "
                        "is not supported for the moment.")
                        % (bline.currency_id.name, company_currency.name))

                partner_ml_vals = self._prepare_move_line_partner_account(
                    bline)
                mvals['line_ids'].append((0, 0, partner_ml_vals))
            trf_ml_vals = self._prepare_move_line_transfer_account(
                total_amount, blines)
            mvals['line_ids'].append((0, 0, trf_ml_vals))
            move = am_obj.create(mvals)
            blines.reconcile_payment_lines()
            move.post()
