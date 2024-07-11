# -*- coding: utf-8 -*-
# Part of BrowseInfo. See LICENSE file for full copyright and licensing details.

import babel
import pytz
from datetime import date, datetime, time, timedelta
from dateutil.relativedelta import relativedelta
from pytz import timezone
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT as default
from odoo import api, fields, models, tools, _
from odoo.exceptions import UserError, ValidationError

class HrPayslip(models.Model):
    _name = 'hr.payslip'
    _description = 'Pay Slip'

    struct_id = fields.Many2one('hr.payroll.structure', string='Structure',
        readonly=True, states={'draft': [('readonly', False)]},
        help='Defines the rules that have to be applied to this payslip, accordingly '
             'to the contract chosen. If you let empty the field contract, this field isn\'t '
             'mandatory anymore and thus the rules applied will be all the rules set on the '
             'structure of all contracts of the employee valid for the chosen period')
    name = fields.Char(string='Payslip Name', readonly=True,
        states={'draft': [('readonly', False)]})
    number = fields.Char(string='Reference', readonly=True, copy=False,
        states={'draft': [('readonly', False)]})
    employee_id = fields.Many2one('hr.employee', string='Employee', required=True, readonly=True,
        states={'draft': [('readonly', False)]})
    date_from = fields.Date(string='Date From', readonly=True, required=True,
        default=lambda self: fields.Date.to_string(date.today().replace(day=1)), states={'draft': [('readonly', False)]})
    date_to = fields.Date(string='Date To', readonly=True, required=True,
        default=lambda self: fields.Date.to_string((datetime.now() + relativedelta(months=+1, day=1, days=-1)).date()),
        states={'draft': [('readonly', False)]})
    # this is chaos: 4 states are defined, 3 are used ('verify' isn't) and 5 exist ('confirm' seems to have existed)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('verify', 'Waiting'),
        ('done', 'Done'),
        ('cancel', 'Rejected'),
    ], string='Status', index=True, readonly=True, copy=False, default='draft',
        help="""* When the payslip is created the status is \'Draft\'
                \n* If the payslip is under verification, the status is \'Waiting\'.
                \n* If the payslip is confirmed then status is set to \'Done\'.
                \n* When user cancel payslip the status is \'Rejected\'.""")
    line_ids = fields.One2many('hr.payslip.line', 'slip_id', string='Payslip Lines', readonly=True,
        states={'draft': [('readonly', False)]})
    company_id = fields.Many2one('res.company', string='Company', readonly=True, copy=False,
        default=lambda self: self.env['res.company']._company_default_get(),
        states={'draft': [('readonly', False)]})
    worked_days_line_ids = fields.One2many('hr.payslip.worked_days', 'payslip_id',
        string='Payslip Worked Days', copy=True, readonly=True,
        states={'draft': [('readonly', False)]})
    input_line_ids = fields.One2many('hr.payslip.input', 'payslip_id', string='Payslip Inputs',
        readonly=True, states={'draft': [('readonly', False)]})
    paid = fields.Boolean(string='Made Payment Order ? ', readonly=True, copy=False,
        states={'draft': [('readonly', False)]})
    note = fields.Text(string='Internal Note', readonly=True, states={'draft': [('readonly', False)]})
    contract_id = fields.Many2one('hr.contract', string='Contract', readonly=True,
        states={'draft': [('readonly', False)]})
    details_by_salary_rule_category = fields.One2many('hr.payslip.line',
        compute='_compute_details_by_salary_rule_category', string='Details by Salary Rule Category')
    credit_note = fields.Boolean(string='Credit Note', readonly=True,
        states={'draft': [('readonly', False)]},
        help="Indicates this payslip has a refund of another")
    payslip_run_id = fields.Many2one('hr.payslip.run', string='Payslip Batches', readonly=True,
        copy=False, states={'draft': [('readonly', False)]})
    payslip_count = fields.Integer(compute='_compute_payslip_count', string="Payslip Computation Details")

    def _compute_details_by_salary_rule_category(self):
        for payslip in self:
            payslip.details_by_salary_rule_category = payslip.mapped('line_ids').filtered(lambda line: line.category_id.id)
            
    def _compute_payslip_count(self):
        for payslip in self:
            payslip.payslip_count = len(payslip.line_ids)

    def payslip_line_count(self):
        self.ensure_one()
        return {
            'name': 'Payslip Computation Details',
            'type': 'ir.actions.act_window',
            'view_mode': 'tree,form',
            'res_model': 'hr.payslip.line',
            "context": {'default_slip_id': [self.id], 'search_default_slip_id': self.id},
        }

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        if any(self.filtered(lambda payslip: payslip.date_from > payslip.date_to)):
            raise ValidationError(_("Payslip 'Date From' must be earlier 'Date To'."))

    @api.returns('self', lambda value: value.id)
    def copy(self, default=None):
        rec = super(HrPayslip, self).copy(default)
        for l in self.input_line_ids:
            l.copy({'payslip_id': rec.id})
        for l in self.line_ids:
            l.copy({'slip_id': rec.id, 'input_ids': []})
        return rec

    def action_payslip_draft(self):
        return self.write({'state': 'draft'})

    def action_payslip_done(self):
        if not self.env.context.get('without_compute_sheet'):
            self.compute_sheet()
        return self.write({'state': 'done'})

    def action_payslip_cancel(self):
        if self.filtered(lambda slip: slip.state == 'done'):
            raise UserError(_("Cannot cancel a payslip that is done."))
        return self.write({'state': 'cancel'})

    def refund_sheet(self):
        for payslip in self:
            copied_payslip = payslip.copy({'credit_note': True, 'name': _('Refund: ') + payslip.name})
            number = copied_payslip.number or self.env['ir.sequence'].next_by_code('salary.slip')
            copied_payslip.write({'number': number})
            copied_payslip.with_context(without_compute_sheet=True).action_payslip_done()
        formview_ref = self.env.ref('bi_hr_payroll.view_hr_payslip_form', False)
        treeview_ref = self.env.ref('bi_hr_payroll.view_hr_payslip_tree', False)
        return {
            'name': ("Refund Payslip"),
            'view_mode': 'tree, form',
            'view_id': False,
            'view_type': 'form',
            'res_model': 'hr.payslip',
            'type': 'ir.actions.act_window',
            'target': 'current',
            'domain': "[('id', 'in', %s)]" % copied_payslip.ids,
            'views': [(treeview_ref and treeview_ref.id or False, 'tree'), (formview_ref and formview_ref.id or False, 'form')],
            'context': {}
        }

    def check_done(self):
        return True

    def unlink(self):
        if any(self.filtered(lambda payslip: payslip.state not in ('draft', 'cancel'))):
            raise UserError(_('You cannot delete a payslip which is not draft or cancelled!'))
        return super(HrPayslip, self).unlink()

    # TODO move this function into hr_contract module, on hr.employee object
    @api.model
    def get_contract(self, employee, date_from, date_to):
        # a contract is valid if it ends between the given dates
        clause_1 = ['&', ('date_end', '<=', date_to), ('date_end', '>=', date_from)]
        # OR if it starts between the given dates
        clause_2 = ['&', ('date_start', '<=', date_to), ('date_start', '>=', date_from)]
        # OR if it starts before the date_from and finish after the date_end (or never finish)
        clause_3 = ['&', ('date_start', '<=', date_from), '|', ('date_end', '=', False), ('date_end', '>=', date_to)]
        clause_final = [('employee_id', '=', employee.id), ('state', '=', 'open'), '|', '|'] + clause_1 + clause_2 + clause_3
        return self.env['hr.contract'].search(clause_final).ids

    def is_not_six_months_passed(start_date_str):
        # Convert the start date string to a datetime object
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')

        # Get the current date
        current_date = datetime.now()

        # Calculate the date 6 months from the start date
        six_months_after_start = start_date + timedelta(days=30*6)

        # Check if the current date is greater than or equal to 6 months after the start date
        return current_date <= six_months_after_start

    def compute_sheet(self):
        for payslip in self:
            number = payslip.number or self.env['ir.sequence'].next_by_code('salary.slip')
            # delete old payslip lines
            payslip.line_ids.unlink()
            # set the list of contract for which the rules have to be applied
            # if we don't give the contract, then the rules to apply should be for all current contracts of the employee
            contract_ids = payslip.contract_id.ids or \
                self.get_contract(payslip.employee_id, payslip.date_from, payslip.date_to)
            lines = [(0, 0, line) for line in self._get_payslip_lines(contract_ids, payslip.id)]
            payslip.write({'line_ids': lines, 'number': number})

            # uang_sewa = 0
            # uang_makan = 0
            # uang_bensin = 0
            # uang_upah = 0
            # potongan_amount = 0
            # uang_sewa_amount = 0
                                                                
            # uang_makan = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('code', '=', 'UANG_MAKAN')])
            # # basic = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('code', '=', 'BASIC')]).amount
            # uang_makanB = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('code', '=', 'UANG_MAKAN')])
            # uang_bensin = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('code', '=', 'UB')])
            # uang_sewa = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('code', '=', 'USM')])
            # uang_upah = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('code', '=', 'UPAHM')])
            
            # potongan = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('code', '=', 'POTONGAN')])
            # basic = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('code', '=', 'BASIC')])
            # basic_amount = basic.amount
            # raise UserError(f'{basic_amount}')






            hadir = 0
            result=[]
            local_timezone = pytz.timezone('Asia/Jakarta')
            att = self.env['hr.attendance'].search([('employee_id', '=', payslip.employee_id.id)])
            pid = self.env['hr.payslip'].search([('employee_id', '=', payslip.employee_id.id)])
            nama = payslip.name
            date_from = payslip.date_from.strftime('%d/%m/%Y')
            date_to = payslip.date_to.strftime('%d/%m/%Y')
            gaji = self.env['hr.contract'].search([('id', '=', payslip.contract_id.id)]).wage
        

            if att:
                for line in att:
                    # Assuming payslip.date_from and payslip.date_to are datetime.date objects
                    check_in = line.check_in
                    # Convert check_in to UTC and then to local timezone
                    local_checkin = check_in.astimezone(local_timezone)
                    # Use datetime.datetime directly for comparison
                    if payslip.date_from <= local_checkin.date() <= payslip.date_to:
                        result.append(line)
            hadir = len(result)
            pids = ', '.join(str(p.id) for p in pid)  # Mengambil ID dari setiap hr.payslip yang ditemukan

            # raise UserError(f'ID : {pids}\nPayslip Name : {nama}\nDate Range : {date_from} - {date_to}\nGaji Bulanan : {gaji}\nJumlah Kehadiran: {hadir}')
            












            # uang_makan_amount = hadir * uang_makan.amount
            # uang_makan.write({'total': uang_makan_amount})
            
            # uang_upah_amount = hadir * uang_upah.amount
            # uang_upah.write({'total': uang_upah_amount})
            
            # uang_sewa_amount = hadir * uang_sewa_amount

            # # raise UserError(f'{uang_sewa_amount}')
            # uang_sewa.write({'total': uang_sewa_amount})
            
            # uang_bensin_amount = hadir * uang_bensin.amount
            # uang_bensin.write({'total': uang_bensin_amount})
            
            # potongan_amount = uang_makan_amount + uang_bensin_amount + uang_sewa_amount + uang_upah_amount
            
            # potongan.write({'amount': potongan_amount})
            # potongan.write({'total': potongan_amount})

            # bpjss_alw = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('code', 'in', ['BPJS_ALW'])])
            # bpjss_ded = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('code', 'in', ['BPJS_DED'])])
            # bpjs_alw = bpjss_alw.amount
            # bpjs_ded = bpjss_ded.amount
            # start_contact = payslip.contract_id.date_start

            # # Convert the start date string to a datetime object
            # start_date = datetime.strptime(str(start_contact), '%Y-%m-%d')

            # # Get the current date
            # current_date = datetime.now()

            # # Calculate the date 6 months from the start date
            # six_months_after_start = start_date + timedelta(days=30*6)


            # if current_date <= six_months_after_start:
            #     bpjs_alw = 0
            #     bpjs_ded = 0
            #     bpjss_ded.write({'amount': bpjs_ded})
            #     bpjss_alw.write({'amount': bpjs_alw})
            # basic = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('category_id', '=', 1)])            
            # # pph = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('code', 'in', ['PPH21'])])    
            # alw = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('category_id', '=', 2)])
            # deb = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('category_id', '=', 4)])
            
            
            # ded_amount = bpjs_ded
            # alw_amount = bpjs_alw
            # for alw_lines in alw:
            #     alw_amount += alw_lines.amount

            # for ded_lines in deb:
            #     ded_amount += ded_lines.amount

            # gross = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('category_id', '=', 3)])            
            # # gross.write({'amount': basic.amount})
            # # self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('code', '=', 'GROSS')]).write({'amount': basic})
            # # gross.write({'amount': basic.amount + potongan.amount})
            # net = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('category_id', '=', 5)])
            # #       
            
            
            # gross = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('category_id', '=', 3)])
            # net = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('category_id', '=', 5)])
            # date_to = str(payslip.date_to).split("-")[1]
            # pph.write({'amount': date_to})
            # if date_to != '12':
            #     basic_amount = 0.0
            #     alw_amount = 0.0
            #     for b in basic:
            #   net.write({'amount': basic.amount + alw_amount - ded_amount})        basic_amount += b.amount

            #     if payslip.contract_id.pph_kategori:
            #         amount = (payslip.contract_id.pph_kategori.tarif_pajak / 100) * gross.amount
            #         pph.write({'amount': amount})   
            #         # gaji_gross = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('code', 'in', ['GROSS'])])
            #         #gaji_gross.write({'amount' : gaji_gross.amount - amount})
            #         gaji_net = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('code', 'in', ['NET'])])
            #         pph21 = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('code', '=', 'PPH21')])
            #         # gaji_net.write({'amount' : gaji_net.amount - amount })
            #         gaji_net.write({'amount': basic.amount - pph21.amount})

            
            # else:
            #     # pph.write({'amount': date_to})

            #     basic_amount = 0.0
            #     alw_amount = 0.0
            #     deb_amount = 0.0
            #     pph_amount = 0.0
            #     self._cr.execute("SELECT * FROM hr_payslip WHERE date_from >= '2024-01-01' and date_to <= '2024-11-30' and employee_id = "+ str(payslip.employee_id.id))
            #     for line in self._cr.dictfetchall():
            #         pphs = self.env['hr.payslip.line'].search([('slip_id', '=', int(line['id'])), ('code', 'in', ['PPH21'])])
            #         basic = self.env['hr.payslip.line'].search([('slip_id', '=', int(line['id'])), ('category_id', '=', 1)])
            #         alw = self.env['hr.payslip.line'].search([('slip_id', '=', int(line['id'])), ('category_id', '=', 2)])
            #         deb = self.env['hr.payslip.line'].search([('slip_id', '=', int(line['id'])), ('category_id', '=', 4)])
            #         bpjs_alw = self.env['hr.payslip.line'].search([('slip_id', '=', int(line['id'])), ('code', 'in', ['BPJS_ALW'])])
            #         bpjs_ded = self.env['hr.payslip.line'].search([('slip_id', '=', int(line['id'])), ('code', 'in', ['BPJS_DED'])])
            #         uang_makan = self.env['hr.payslip.line'].search([('slip_id', '=', int(line['id'])), ('code', '=', 'UANG_MAKAN')])

            #         for b in basic:
            #             basic_amount += b.amount

            #         for a in alw:
            #             alw_amount += a.amount

            #         for c in deb:
            #             deb_amount += c.amount

            #         for d in pphs:
            #             pph_amount += d.amount
                    
            #         for e in bpjs_alw:


            #     basic_amount += self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('category_id', '=', 1)]).amount or 0.0
            #     # for alw_des in self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('category_id', '=', 2)]):
            #     #     alw_amount += alw_des.amount
            #     # for deb_des in self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('category_id', '=', 4), ('code', '!=', 'PPH_21_BARU')]):
            #     #     deb_amount += deb_des.amount


            #     if payslip.contract_id.ptkp_id:
            #         gapok = basic_amount + alw_amount
            #         gajiPokok = gapok
            #         biayaJabatan = (5/100 * gapok)
            #         netto = gajiPokok - biayaJabatan - ((deb_amount))
            #         penghasilanKenaPajak = netto - payslip.contract_id.ptkp_id.nominal

            #         # pph = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('code', 'in', ['PPH_21_BARU'])])
            #         # pph.write({'amount': penghasilanKenaPajak})

            #         if penghasilanKenaPajak < 60000000:
            #             amount = (5/100) * penghasilanKenaPajak
            #             penghasilanKenaPajak -= penghasilanKenaPajak
            #         elif penghasilanKenaPajak >= 60000000:
            #             amount = (5/100) * 60000000
            #             penghasilanKenaPajak -= 60000000
            #         if penghasilanKenaPajak < 250000000:
            #             amount += (15 / 100) * penghasilanKenaPajak
            #             penghasilanKenaPajak -= penghasilanKenaPajak
            #         elif penghasilanKenaPajak >= 250000000:
            #             amount += (15 / 100) * 250000000
            #             penghasilanKenaPajak -= 250000000
            #         if penghasilanKenaPajak < 500000000:
            #             amount += (25 / 100) * penghasilanKenaPajak
            #             penghasilanKenaPajak -= penghasilanKenaPajak
            #         elif penghasilanKenaPajak >= 500000000:
            #             amount += (25 / 100) * 500000000
            #             penghasilanKenaPajak -= 500000000
            #         if penghasilanKenaPajak != 0.0:
            #             while True:
            #                 if penghasilanKenaPajak < 500000000:
            #                     amount += (30 / 100) * penghasilanKenaPajak
            #                     penghasilanKenaPajak -= penghasilanKenaPajak
            #                 else:
            #                     amount += (30 / 100) * 500000000
            #                     penghasilanKenaPajak -= 500000000

            #                 if penghasilanKenaPajak <= 0.0:
            #                     break

            #         result = amount - (pph_amount)
            #         pph = self.env['hr.payslip.line'].search([('slip_id', '=', int(payslip.id)), ('code', 'in', ['PPH_21_BARU'])])
            #         pph.write({'amount': result})

        return True

    @api.model
    def get_worked_day_lines(self, contracts, date_from, date_to):
        res = []
        # fill only if the contract as a working schedule linked
        for contract in contracts.filtered(lambda contract: contract.resource_calendar_id):
            day_from = datetime.combine(fields.Date.from_string(date_from), time.min)
            day_to = datetime.combine(fields.Date.from_string(date_to), time.max)

            # compute leave days
            leaves = {}
            calendar = contract.resource_calendar_id
            tz = timezone(calendar.tz)
            day_leave_intervals = contract.employee_id.list_leaves(day_from, day_to, calendar=contract.resource_calendar_id)
            for day, hours, leave in day_leave_intervals:
                holiday = leave[:1].holiday_id
                current_leave_struct = leaves.setdefault(holiday.holiday_status_id, {
                    'name': holiday.holiday_status_id.name or _('Global Leaves'),
                    'sequence': 5,
                    'code': holiday.holiday_status_id.name or 'GLOBAL',
                    'number_of_days': 0.0,
                    'number_of_hours': 0.0,
                    'contract_id': contract.id,
                })
                current_leave_struct['number_of_hours'] += hours
                work_hours = calendar.get_work_hours_count(
                    tz.localize(datetime.combine(day, time.min)),
                    tz.localize(datetime.combine(day, time.max)),
                    compute_leaves=False,
                )
                if work_hours:
                    current_leave_struct['number_of_days'] += hours / work_hours

            # compute worked days
            work_data = contract.employee_id._get_work_days_data_batch(day_from, day_to, calendar=contract.resource_calendar_id)
            attendances = {
                'name': _("Normal Working Days paid at 100%"),
                'sequence': 1,
                'code': 'WORK100',
                'number_of_days': work_data[contract.employee_id.id]['days'],
                'number_of_hours': work_data[contract.employee_id.id]['hours'],
                'contract_id': contract.id,
            }

            res.append(attendances)
            res.extend(leaves.values())
        return res

    @api.model
    def get_inputs(self, contracts, date_from, date_to):
        res = []

        structure_ids = contracts.get_all_structures()
        rule_ids = self.env['hr.payroll.structure'].browse(structure_ids).get_all_rules()
        sorted_rule_ids = [id for id, sequence in sorted(rule_ids, key=lambda x:x[1])]
        inputs = self.env['hr.salary.rule'].browse(sorted_rule_ids).mapped('input_ids')

        for contract in contracts:
            for input in inputs:
                input_data = {
                    'name': input.name,
                    'code': input.code,
                    'contract_id': contract.id,
                }
                res += [input_data]
        return res

    @api.model
    def _get_payslip_lines(self, contract_ids, payslip_id):
        def _sum_salary_rule_category(localdict, category, amount):
            if category.parent_id:
                localdict = _sum_salary_rule_category(localdict, category.parent_id, amount)

            if category.code in localdict['categories'].dict:
                localdict['categories'].dict[category.code] += amount
            else:
                localdict['categories'].dict[category.code] = amount

            return localdict

        class BrowsableObject(object):
            def __init__(self, employee_id, dict, env):
                self.employee_id = employee_id
                self.dict = dict
                self.env = env

            def __getattr__(self, attr):
                return attr in self.dict and self.dict.__getitem__(attr) or 0.0

        class InputLine(BrowsableObject):
            """a class that will be used into the python code, mainly for usability purposes"""
            def sum(self, code, from_date, to_date=None):
                if to_date is None:
                    to_date = fields.Date.today()
                self.env.cr.execute("""
                    SELECT sum(amount) as sum
                    FROM hr_payslip as hp, hr_payslip_input as pi
                    WHERE hp.employee_id = %s AND hp.state = 'done'
                    AND hp.date_from >= %s AND hp.date_to <= %s AND hp.id = pi.payslip_id AND pi.code = %s""",
                    (self.employee_id, from_date, to_date, code))
                return self.env.cr.fetchone()[0] or 0.0

        class WorkedDays(BrowsableObject):
            """a class that will be used into the python code, mainly for usability purposes"""
            def _sum(self, code, from_date, to_date=None):
                if to_date is None:
                    to_date = fields.Date.today()
                self.env.cr.execute("""
                    SELECT sum(number_of_days) as number_of_days, sum(number_of_hours) as number_of_hours
                    FROM hr_payslip as hp, hr_payslip_worked_days as pi
                    WHERE hp.employee_id = %s AND hp.state = 'done'
                    AND hp.date_from >= %s AND hp.date_to <= %s AND hp.id = pi.payslip_id AND pi.code = %s""",
                    (self.employee_id, from_date, to_date, code))
                return self.env.cr.fetchone()

            def sum(self, code, from_date, to_date=None):
                res = self._sum(code, from_date, to_date)
                return res and res[0] or 0.0

            def sum_hours(self, code, from_date, to_date=None):
                res = self._sum(code, from_date, to_date)
                return res and res[1] or 0.0

        class Payslips(BrowsableObject):
            """a class that will be used into the python code, mainly for usability purposes"""

            def sum(self, code, from_date, to_date=None):
                if to_date is None:
                    to_date = fields.Date.today()
                self.env.cr.execute("""SELECT sum(case when hp.credit_note = False then (pl.total) else (-pl.total) end)
                            FROM hr_payslip as hp, hr_payslip_line as pl
                            WHERE hp.employee_id = %s AND hp.state = 'done'
                            AND hp.date_from >= %s AND hp.date_to <= %s AND hp.id = pl.slip_id AND pl.code = %s""",
                            (self.employee_id, from_date, to_date, code))
                res = self.env.cr.fetchone()
                return res and res[0] or 0.0

        #we keep a dict with the result because a value can be overwritten by another rule with the same code
        result_dict = {}
        rules_dict = {}
        worked_days_dict = {}
        inputs_dict = {}
        blacklist = []
        payslip = self.env['hr.payslip'].browse(payslip_id)
        for worked_days_line in payslip.worked_days_line_ids:
            worked_days_dict[worked_days_line.code] = worked_days_line
        for input_line in payslip.input_line_ids:
            inputs_dict[input_line.code] = input_line

        categories = BrowsableObject(payslip.employee_id.id, {}, self.env)
        inputs = InputLine(payslip.employee_id.id, inputs_dict, self.env)
        worked_days = WorkedDays(payslip.employee_id.id, worked_days_dict, self.env)
        payslips = Payslips(payslip.employee_id.id, payslip, self.env)
        rules = BrowsableObject(payslip.employee_id.id, rules_dict, self.env)

        baselocaldict = {'categories': categories, 'rules': rules, 'payslip': payslips, 'worked_days': worked_days, 'inputs': inputs}
        #get the ids of the structures on the contracts and their parent id as well
        contracts = self.env['hr.contract'].browse(contract_ids)
        if len(contracts) == 1 and payslip.struct_id:
            structure_ids = list(set(payslip.struct_id._get_parent_structure().ids))
        else:
            structure_ids = contracts.get_all_structures()
        #get the rules of the structure and thier children
        rule_ids = self.env['hr.payroll.structure'].browse(structure_ids).get_all_rules()
        #run the rules by sequence
        sorted_rule_ids = [id for id, sequence in sorted(rule_ids, key=lambda x:x[1])]
        sorted_rules = self.env['hr.salary.rule'].browse(sorted_rule_ids)

        for contract in contracts:
            employee = contract.employee_id
            localdict = dict(baselocaldict, employee=employee, contract=contract)
            for rule in sorted_rules:
                key = rule.code + '-' + str(contract.id)
                localdict['result'] = None
                localdict['result_qty'] = 1.0
                localdict['result_rate'] = 100
                #check if the rule can be applied
                if rule._satisfy_condition(localdict) and rule.id not in blacklist:
                    #compute the amount of the rule
                    amount, qty, rate = rule._compute_rule(localdict)
                    #check if there is already a rule computed with that code
                    previous_amount = rule.code in localdict and localdict[rule.code] or 0.0
                    #set/overwrite the amount computed for this rule in the localdict
                    tot_rule = amount * qty * rate / 100.0
                    localdict[rule.code] = tot_rule
                    rules_dict[rule.code] = rule
                    #sum the amount for its salary category
                    localdict = _sum_salary_rule_category(localdict, rule.category_id, tot_rule - previous_amount)
                    #create/overwrite the rule in the temporary results
                    result_dict[key] = {
                        'salary_rule_id': rule.id,
                        'contract_id': contract.id,
                        'name': rule.name,
                        'code': rule.code,
                        'category_id': rule.category_id.id,
                        'sequence': rule.sequence,
                        'appears_on_payslip': rule.appears_on_payslip,
                        'condition_select': rule.condition_select,
                        'condition_python': rule.condition_python,
                        'condition_range': rule.condition_range,
                        'condition_range_min': rule.condition_range_min,
                        'condition_range_max': rule.condition_range_max,
                        'amount_select': rule.amount_select,
                        'amount_fix': rule.amount_fix,
                        'amount_python_compute': rule.amount_python_compute,
                        'amount_percentage': rule.amount_percentage,
                        'amount_percentage_base': rule.amount_percentage_base,
                        'register_id': rule.register_id.id,
                        'amount': amount,
                        'employee_id': contract.employee_id.id,
                        'quantity': qty,
                        'rate': rate,
                    }
                else:
                    #blacklist this rule and its children
                    blacklist += [id for id, seq in rule._recursive_search_of_rules()]

        return list(result_dict.values())

    # YTI TODO To rename. This method is not really an onchange, as it is not in any view
    # employee_id and contract_id could be browse records
    def onchange_employee_id(self, date_from, date_to, employee_id=False, contract_id=False):
        #defaults
        res = {
            'value': {
                'line_ids': [],
                #delete old input lines
                'input_line_ids': [(2, x,) for x in self.input_line_ids.ids],
                #delete old worked days lines
                'worked_days_line_ids': [(2, x,) for x in self.worked_days_line_ids.ids],
                #'details_by_salary_head':[], TODO put me back
                'name': '',
                'contract_id': False,
                'struct_id': False,
            }
        }
        if (not employee_id) or (not date_from) or (not date_to):
            return res
        ttyme = datetime.combine(fields.Date.from_string(date_from), time.min)
        employee = self.env['hr.employee'].browse(employee_id)
        locale = self.env.context.get('lang') or 'en_US'
        res['value'].update({
            'name': _('Salary Slip of %s for %s') % (employee.name, tools.ustr(babel.dates.format_date(date=ttyme, format='MMMM-y', locale=locale))),
            'company_id': employee.company_id.id,
        })

        if not self.env.context.get('contract'):
            #fill with the first contract of the employee
            contract_ids = self.get_contract(employee, date_from, date_to)
        else:
            if contract_id:
                #set the list of contract for which the input have to be filled
                contract_ids = [contract_id]
            else:
                #if we don't give the contract, then the input to fill should be for all current contracts of the employee
                contract_ids = self.get_contract(employee, date_from, date_to)

        if not contract_ids:
            return res
        contract = self.env['hr.contract'].browse(contract_ids[0])
        res['value'].update({
            'contract_id': contract.id
        })
        struct = contract.struct_id
        if not struct:
            return res
        res['value'].update({
            'struct_id': struct.id,
        })
        #computation of the salary input
        contracts = self.env['hr.contract'].browse(contract_ids)
        worked_days_line_ids = self.get_worked_day_lines(contracts, date_from, date_to)
        input_line_ids = self.get_inputs(contracts, date_from, date_to)
        res['value'].update({
            'worked_days_line_ids': worked_days_line_ids,
            'input_line_ids': input_line_ids,
        })
        return res

    @api.onchange('employee_id', 'date_from', 'date_to')
    def onchange_employee(self):

        if (not self.employee_id) or (not self.date_from) or (not self.date_to):
            return

        employee = self.employee_id
        date_from = self.date_from
        date_to = self.date_to
        contract_ids = []

        ttyme = datetime.combine(fields.Date.from_string(date_from), time.min)
        locale = self.env.context.get('lang') or 'en_US'
        self.name = _('Salary Slip of %s for %s') % (employee.name, tools.ustr(babel.dates.format_date(date=ttyme, format='MMMM-y', locale=locale)))
        self.company_id = employee.company_id

        if not self.env.context.get('contract') or not self.contract_id:
            contract_ids = self.get_contract(employee, date_from, date_to)
            if not contract_ids:
                return
            self.contract_id = self.env['hr.contract'].browse(contract_ids[0])

        if not self.contract_id.struct_id:
            return
        self.struct_id = self.contract_id.struct_id

        #computation of the salary input
        contracts = self.env['hr.contract'].browse(contract_ids)
        worked_days_line_ids = self.get_worked_day_lines(contracts, date_from, date_to)
        worked_days_lines = self.worked_days_line_ids.browse([])
        for r in worked_days_line_ids:
            worked_days_lines += worked_days_lines.new(r)
        self.worked_days_line_ids = worked_days_lines

        input_line_ids = self.get_inputs(contracts, date_from, date_to)
        input_lines = self.input_line_ids.browse([])
        for r in input_line_ids:
            input_lines += input_lines.new(r)
        self.input_line_ids = input_lines
        return

    @api.onchange('contract_id')
    def onchange_contract(self):
        if not self.contract_id:
            self.struct_id = False
        self.with_context(contract=True).onchange_employee()
        return

    def get_salary_line_total(self, code):
        self.ensure_one()
        line = self.line_ids.filtered(lambda line: line.code == code)
        if line:
            return line[0].total
        else:
            return 0.0


class HrPayslipLine(models.Model):
    _name = 'hr.payslip.line'
    _inherit = 'hr.salary.rule'
    _description = 'Payslip Line'
    _order = 'contract_id, sequence'

    slip_id = fields.Many2one('hr.payslip', string='Pay Slip', required=True, ondelete='cascade')
    salary_rule_id = fields.Many2one('hr.salary.rule', string='Rule', required=True)
    employee_id = fields.Many2one('hr.employee', string='Employee', required=True)
    contract_id = fields.Many2one('hr.contract', string='Contract', required=True, index=True)
    rate = fields.Float(string='Rate (%)', digits='Payroll Rate', default=100.0)
    amount = fields.Float(digits='Payroll')
    quantity = fields.Float(digits='Payroll', default=1.0)
    total = fields.Float(compute='_compute_total', string='Total', digits='Payroll', store=True)

    @api.depends('quantity', 'amount', 'rate')
    def _compute_total(self):
        for line in self:  # Tambahkan titik dua (:) di akhir baris ini
            line.total = float(line.quantity) * line.amount * line.rate / 100

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if 'employee_id' not in values or 'contract_id' not in values:
                payslip = self.env['hr.payslip'].browse(values.get('slip_id'))
                values['employee_id'] = values.get('employee_id') or payslip.employee_id.id
                values['contract_id'] = values.get('contract_id') or payslip.contract_id and payslip.contract_id.id
                if not values['contract_id']:
                    raise UserError(_('You must set a contract to create a payslip line.'))
        return super(HrPayslipLine, self).create(vals_list)


class HrPayslipWorkedDays(models.Model):
    _name = 'hr.payslip.worked_days'
    _description = 'Payslip Worked Days'
    _order = 'payslip_id, sequence'

    name = fields.Char(string='Description', required=True)
    payslip_id = fields.Many2one('hr.payslip', string='Pay Slip', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(required=True, index=True, default=10)
    code = fields.Char(required=True, help="The code that can be used in the salary rules")
    number_of_days = fields.Float(string='Number of Days')
    number_of_hours = fields.Float(string='Number of Hours')
    contract_id = fields.Many2one('hr.contract', string='Contract', required=True,
        help="The contract for which applied this input")


class HrPayslipInput(models.Model):
    _name = 'hr.payslip.input'
    _description = 'Payslip Input'
    _order = 'payslip_id, sequence'

    name = fields.Char(string='Description', required=True)
    payslip_id = fields.Many2one('hr.payslip', string='Pay Slip', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(required=True, index=True, default=10)
    code = fields.Char(required=True, help="The code that can be used in the salary rules")
    amount = fields.Float(help="It is used in computation. For e.g. A rule for sales having "
                               "1% commission of basic salary for per product can defined in expression "
                               "like result = inputs.SALEURO.amount * contract.wage*0.01.")
    contract_id = fields.Many2one('hr.contract', string='Contract', required=True,
        help="The contract for which applied this input")


class HrPayslipRun(models.Model):
    _name = 'hr.payslip.run'
    _description = 'Payslip Batches'

    name = fields.Char(required=True, readonly=True, states={'draft': [('readonly', False)]})
    slip_ids = fields.One2many('hr.payslip', 'payslip_run_id', string='Payslips', readonly=True,
        states={'draft': [('readonly', False)]})
    state = fields.Selection([
        ('draft', 'Draft'),
        ('close', 'Close'),
    ], string='Status', index=True, readonly=True, copy=False, default='draft')
    date_start = fields.Date(string='Date From', required=True, readonly=True,
        states={'draft': [('readonly', False)]}, default=lambda self: fields.Date.to_string(date.today().replace(day=1)))
    date_end = fields.Date(string='Date To', required=True, readonly=True,
        states={'draft': [('readonly', False)]},
        default=lambda self: fields.Date.to_string((datetime.now() + relativedelta(months=+1, day=1, days=-1)).date()))
    credit_note = fields.Boolean(string='Credit Note', readonly=True,
        states={'draft': [('readonly', False)]},
        help="If its checked, indicates that all payslips generated from here are refund payslips.")

    def draft_payslip_run(self):
        return self.write({'state': 'draft'})

    def close_payslip_run(self):
        return self.write({'state': 'close'})