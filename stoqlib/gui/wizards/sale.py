# -*- coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2005, 2006 Async Open Source <http://www.async.com.br>
## All rights reserved
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU Lesser General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU Lesser General Public License for more details.
##
## You should have received a copy of the GNU Lesser General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., or visit: http://www.gnu.org/.
##
## Author(s):   Evandro Vale Miquelito      <evandro@async.com.br>
##
##
""" Sale wizard definition """

from kiwi.ui.widgets.list import Column, SummaryLabel
from kiwi.datatypes import currency
from kiwi.python import Settable

from stoqlib.exceptions import DatabaseInconsistency
from stoqlib.lib.translation import stoqlib_gettext
from stoqlib.lib.validators import get_formatted_price
from stoqlib.lib.parameters import sysparam
from stoqlib.lib.drivers import (print_cheques_for_payment_group,
                                 check_virtual_printer_for_current_host)
from stoqlib.lib.defaults import (METHOD_MONEY, METHOD_MULTIPLE,
                                  METHOD_GIFT_CERTIFICATE)
from stoqlib.gui.base.dialogs import run_dialog, notify_dialog
from stoqlib.gui.base.wizards import BaseWizardStep, BaseWizard
from stoqlib.gui.base.editors import NoteEditor
from stoqlib.gui.base.lists import AdditionListSlave
from stoqlib.gui.slaves.paymentmethod import (SelectCashMethodSlave,
                                              SelectPaymentMethodSlave)
from stoqlib.gui.slaves.sale import DiscountChargeSlave
from stoqlib.gui.slaves.payment import (CheckMethodSlave, BillMethodSlave,
                                        CardMethodSlave,
                                        FinanceMethodSlave)
from stoqlib.domain.payment.methods import get_active_pm_ifaces
from stoqlib.domain.person import Person
from stoqlib.domain.sale import Sale
from stoqlib.domain.giftcertificate import (GiftCertificate,
                                            get_volatile_gift_certificate)
from stoqlib.domain.interfaces import (IPaymentGroup, ISalesPerson,
                                       ICheckPM, ICardPM, IBillPM,
                                       IFinancePM, ISellable,
                                       IRenegotiationGiftCertificate,
                                       IRenegotiationOutstandingValue,
                                       IMultiplePM, IMoneyPM,
                                       IGiftCertificatePM)
_ = stoqlib_gettext


#
# Wizard Steps
#

class PaymentMethodStep(BaseWizardStep):
    gladefile = 'PaymentMethodStep'
    model_type = Sale
    slave_holder = 'method_holder'

    slaves_info = ((ICheckPM, CheckMethodSlave),
                   (IBillPM, BillMethodSlave),
                   (ICardPM, CardMethodSlave),
                   (IFinancePM, FinanceMethodSlave))

    def __init__(self, wizard, previous, conn, model,
                 outstanding_value=currency(0)):
        # A dictionary for payment method informaton. Each key is a
        # PaymentMethod adapter and its value is a tuple where the first
        # element is a payment method interface and the second one is the
        # slave class
        self.method_dict = {}
        # A cache for instantiated slaves
        self.slaves_dict = {}
        self.outstanding_value = outstanding_value
        self.group = wizard.get_payment_group()
        self.renegotiation_mode = outstanding_value > currency(0)
        BaseWizardStep.__init__(self, conn, wizard, model, previous)
        self.method_slave = None
        self.setup_combo()
        self._update_payment_method_slave()

    def _set_method_slave(self, slave_class, slave_args):
        if not self.slaves_dict.has_key(slave_class):
            slave = slave_class(*slave_args)
            self.slaves_dict[slave_class] = slave
            if slave_class is BillMethodSlave:
                slave.bank_label.hide()
                slave.bank_combo.hide()
        else:
            slave = self.slaves_dict[slave_class]
        self.method_slave = slave

    def _update_payment_method(self, iface):
        if not self.renegotiation_mode:
            self.group.set_method(iface)
        else:
            method_id = self.group.get_method_id_by_iface(iface)
            adapter = self.group.get_renegotiation_adapter()
            adapter.payment_method = method_id

    def _update_payment_method_slave(self):
        selected = self.method_combo.get_selected_data()
        slave_args = (self.wizard, self, self.conn, self.model,
                      selected, self.outstanding_value)
        if not self.method_dict.has_key(selected):
            raise ValueError('Invalid payment method type: %s'
                             % type(selected))
        iface, slave_class = self.method_dict[selected]
        self._update_payment_method(iface)
        self._set_method_slave(slave_class, slave_args)
        if self.get_slave(self.slave_holder):
            self.detach_slave(self.slave_holder)
        self.attach_slave(self.slave_holder, self.method_slave)

    def setup_combo(self):
        base_method = sysparam(self.conn).BASE_PAYMENT_METHOD
        combo_items = []
        slaves_info = [(iface, slave)
                        for iface, slave in PaymentMethodStep.slaves_info
                            if iface in get_active_pm_ifaces()]
        for iface, slave_class in slaves_info:
            method = iface(base_method, connection=self.conn)
            if method.is_active:
                self.method_dict[method] = iface, slave_class
                combo_items.append((method.description, method))
        self.method_combo.prefill(combo_items)


    #
    # WizardStep hooks
    #

    def validate_step(self):
        self.method_slave.finish()
        return True

    def has_next_step(self):
        return False

    def post_init(self):
        self.method_combo.grab_focus()
        if self.method_slave:
            self.method_slave.update_view()

    #
    # Kiwi callbacks
    #

    def on_method_combo__changed(self, *args):
        self._update_payment_method_slave()


class GiftCertificateOutstandingStep(BaseWizardStep):
    gladefile = 'GiftCertificateOutstandingStep'
    model_type = None

    def __init__(self, wizard, previous, conn, sale, outstanding_value):
        self.outstanding_value = outstanding_value
        self.sale = sale
        self.group = wizard.get_payment_group()
        self._is_last = True
        BaseWizardStep.__init__(self, conn, wizard, previous=previous)
        self.register_validate_function(self.wizard.refresh_next)
        self._setup_widgets()

    def _setup_widgets(self):
        text = (_('Select method of payment for the %s outstanding value')
                % get_formatted_price(self.outstanding_value))
        self.header_label.set_text(text)
        self.header_label.set_size('large')
        self.header_label.set_bold(True)
        group = self.wizard.get_payment_group()
        if not self.wizard.edit_mode:
            return
        adapter = self._get_renegotiation_data()
        money_method = METHOD_MONEY
        if adapter.preview_payment_method == money_method:
            self.cash_check.set_active(True)
        else:
            self.other_methods_check.set_active(True)

    def _get_renegotiation_data(self):
        if self.group.renegotiation_data is None:
            return None
        cert_adapter = self.group.get_renegotiation_adapter()
        if not IRenegotiationOutstandingValue.providedBy(cert_adapter):
            raise ValueError('Invalid adapter for this renegotiation, '
                             'Got %s' % cert_adapter)
        return cert_adapter

    #
    # WizardStep hooks
    #


    def next_step(self):
        if self.other_methods_check.get_active():
            method = METHOD_MULTIPLE
            if self.wizard.skip_payment_step:
                self._is_last = True
        else:
            method = METHOD_MONEY
            self.wizard.setup_cash_payment(self.outstanding_value)

        cert_adapter = self._get_renegotiation_data()
        if not cert_adapter:
            value = self.outstanding_value
            self.group.create_renegotiation_outstanding_data(value,
                                                             method)
        if self._is_last:
            # finish the wizard
            return
        return PaymentMethodStep(self.wizard, self, self.conn, self.sale,
                                 self.outstanding_value)

    def post_init(self):
        self.cash_check.grab_focus()
        self.wizard.enable_finish()

    #
    # Callbacks
    #

    def on_cash_check__toggled(self, *args):
        self._is_last = True
        self.wizard.enable_finish()

    def on_other_methods_check__toggled(self, *args):
        self._is_last = False
        self.wizard.disable_finish()


class GiftCertificateOverpaidStep(BaseWizardStep):
    gladefile = 'GiftCertificateOverpaidStep'
    model_type = Settable
    proxy_widgets = ('certificate_number',)

    def __init__(self, wizard, previous, conn, sale, overpaid_value):
        self.overpaid_value = overpaid_value
        self.sale = sale
        self.group = wizard.get_payment_group()
        BaseWizardStep.__init__(self, conn, wizard, previous=previous)
        self.register_validate_function(self.refresh_next)

    def _setup_widgets(self):
        if not sysparam(self.conn).RETURN_MONEY_ON_SALES:
            self.return_check.set_sensitive(False)
        certificate = sysparam(self.conn).DEFAULT_GIFT_CERTIFICATE_TYPE
        description = certificate.base_sellable_info.description
        text = _('Create a <u>%s</u> with this value') % description
        self.certificate_label.set_text(text)
        header_text = (_('There is %s overpaid') %
                       get_formatted_price(self.overpaid_value))
        self.header_label.set_text(header_text)
        self.header_label.set_size('large')
        self.header_label.set_bold(True)
        if not self.wizard.edit_mode:
            return
        cert_adapter = self.group.get_renegotiation_adapter()
        if IRenegotiationGiftCertificate.providedBy(cert_adapter):
            number = cert_adapter.new_gift_certificate_number
            self.model.number = number
            self.certificate_check.set_active(True)
        else:
            self.return_check.set_active(True)

    def refresh_next(self, validation_value):
        if (self.certificate_check.get_active()
            and not self.model.number):
            self.wizard.disable_next()
            return
        self.wizard.refresh_next(validation_value)

    #
    # BaseEditorSlave hooks
    #

    def create_model(self, conn):
        return get_volatile_gift_certificate()

    def setup_proxies(self):
        self._setup_widgets()
        klass = GiftCertificateOverpaidStep
        self.proxy = self.add_proxy(self.model, klass.proxy_widgets)

    #
    # WizardStep hooks
    #

    def validate_step(self):
        number = self.model.number
        if self.group.renegotiation_data is not None:
            cert_adapter = self.group.get_renegotiation_adapter()
            if IRenegotiationGiftCertificate.providedBy(cert_adapter):
                cert_adapter.new_gift_certificate_number = number
        else:
            value = self.overpaid_value
            if self.return_check.get_active():
                self.group.create_renegotiation_return_data(value)
            else:
                self.group.create_renegotiation_giftcertificate_data(number,
                                                                     value)
        return True

    def has_next_step(self):
        return False

    def post_init(self):
        self.certificate_number.grab_focus()
        self._update_widgets()

    #
    # Callbacks
    #

    def _update_widgets(self):
        can_return_money = self.return_check.get_active()
        self.certificate_number.set_sensitive(not can_return_money)
        self.force_validation()

    def after_certificate_number__changed(self, *args):
        self._update_widgets()

    def on_certificate_check__toggled(self, *args):
        self._update_widgets()

    def on_return_check__toggled(self, *args):
        self._update_widgets()


class GiftCertificateSelectionStep(BaseWizardStep):
    gladefile = 'GiftCertificateSelectionStep'
    model_type = Settable
    proxy_widgets = ('certificate_number',)

    def __init__(self, wizard, previous, conn, sale):
        self.sale = sale
        self.table = GiftCertificate.getAdapterClass(ISellable)
        self.sale_total = self.sale.get_total_sale_amount()
        self.group = wizard.get_payment_group()
        BaseWizardStep.__init__(self, conn, wizard, previous=previous)
        self.register_validate_function(self.wizard.refresh_next)
        self._update_total()

    def _setup_widgets(self):
        self.header_label.set_size('large')
        self.header_label.set_bold(True)

        certificates = self.table.get_sold_sellables(self.conn)
        items = [(c.get_short_description(), c) for c in certificates]
        self.certificate_number.prefill(items)

    def _get_columns(self):
        return [Column('code', title=_('Number'), data_type=str, width=90),
                Column('base_sellable_info.description',
                       title=_('Description'),
                       data_type=str, expand=True, searchable=True),
                Column('base_sellable_info.price', title=_('Price'),
                       data_type=currency, width=90)]

    def _get_gift_certificates_total(self):
        total = sum([c.get_price() for c in self.slave.klist],
                    currency(0))
        return currency(total)

    #
    # BaseEditorSlave hooks
    #

    def create_model(self, conn):
        return get_volatile_gift_certificate()

    def setup_proxies(self):
        self._setup_widgets()
        klass = GiftCertificateSelectionStep
        self.proxy = self.add_proxy(self.model, klass.proxy_widgets)

    def setup_slaves(self):
        if self.wizard.edit_mode:
            certificates = list(self.group.get_gift_certificates())
            if not certificates:
                raise ValueError('You should have gift certificates '
                                 'defined at this point.')
        else:
            certificates = []
        self.slave = AdditionListSlave(self.conn, self._get_columns(),
                                       klist_objects=certificates)
        self.slave.hide_edit_button()
        self.slave.hide_add_button()
        self.slave.connect('after-delete-items', self.after_delete_items)
        value_format = '<b>%s</b>'
        self.summary = SummaryLabel(klist=self.slave.klist,
                                    column='base_sellable_info.price',
                                    label=_('<b>Total:</b>'),
                                    value_format=value_format)
        self.summary.show()
        self.slave.list_vbox.pack_start(self.summary, expand=False)
        self.attach_slave('list_holder', self.slave)

    #
    # WizardStep hooks
    #

    def next_step(self):
        if not len(self.slave.klist[:]):
            raise ValueError('You should have at least one gift certificate '
                             'selected at this point')
        for certificate in self.slave.klist[:]:
            self.wizard.gift_certificates.append(certificate)
        gift_total = self._get_gift_certificates_total()
        if gift_total == self.sale_total:
            # finish the wizard
            return
        elif self.sale_total > gift_total:
            outstanding_value = self.sale_total - gift_total
            return GiftCertificateOutstandingStep(self.wizard, self,
                                                  self.conn, self.sale,
                                                  outstanding_value)
        else:
            overpaid_value = gift_total - self.sale_total
            return GiftCertificateOverpaidStep(self.wizard, self,
                                               self.conn, self.sale,
                                               overpaid_value)

    def post_init(self):
        self.certificate_number.grab_focus()

    #
    # Callbacks
    #

    def _update_total(self, *args):
        self.summary.update_total()
        gift_total = self._get_gift_certificates_total()
        if gift_total == self.sale_total:
            text = ''
            value = ''
            self.wizard.enable_finish()
        else:
            value = self.sale_total - gift_total
            if gift_total < self.sale_total:
                text = _('Outstanding:')
            else:
                text = _('Overpaid:')
                value = -value
            value = get_formatted_price(value)
            self.wizard.disable_finish()
        self.difference_label.set_text(text)
        self.difference_value_label.set_text(value)

    def _get_certificate_by_code(self, code):
        certificate = self.table.selectBy(code=code, connection=self.conn)
        qty = certificate.count()
        if not qty:
            msg = _("The gift certificate with code '%s' doesn't "
                    "exists" % code)
            self.certificate_number.set_invalid(msg)
            return
        if qty != 1:
            raise DatabaseInconsistency('You should have only one '
                                        'gift certificate with code %s'
                                        % code)
        return certificate[0]

    def _update_widgets(self):
        has_gift_certificate = self.certificate_number.get_text() != ''
        self.add_button.set_sensitive(has_gift_certificate)

    def _add_item(self):
        certificate = self.proxy.model and self.proxy.model.number
        self.add_button.set_sensitive(False)
        if not certificate:
            code = self.certificate_number.get_text()
            certificate = self._get_certificate_by_code(code)
            if not certificate:
                return
        if certificate in self.slave.klist[:]:
            msg = _("The gift certificate '%s' was already added to the list"
                    % certificate.get_short_description())
            self.certificate_number.set_invalid(msg)
            return
        item = certificate or self.model.number
        self.slave.klist.append(item)
        # As we have a selection extended mode for kiwi list, we
        # need to unselect everything before select the new instance.
        self.slave.klist.unselect_all()
        self.slave.klist.select(item)
        self.certificate_number.set_text('')
        self._update_total()

    def on_add_button__clicked(self, *args):
        self._add_item()

    def on_certificate_number__activate(self, *args):
        if not self.add_button.get_property('sensitive'):
            return
        self._add_item()

    def on_certificate_number__changed(self, *args):
        self._update_widgets()

    def after_certificate_number__changed(self, *args):
        self._update_widgets()

    def after_delete_items(self, *args):
        self._update_total()


class SalesPersonStep(BaseWizardStep):
    gladefile = 'SalesPersonStep'
    model_type = Sale
    proxy_widgets = ('total_lbl',
                     'subtotal_lbl',
                     'invoice_number',
                     'salesperson_combo')

    def __init__(self, wizard, conn, model, edit_mode):
        self._edit_mode = edit_mode
        BaseWizardStep.__init__(self, conn, wizard, model)
        if not self._edit_mode:
            self.model.reset_discount_and_charge()
        self.register_validate_function(self.wizard.refresh_next)
        if not check_virtual_printer_for_current_host(self.conn):
            self.invoice_number.hide()
            self.invoice_label.hide()

    def _setup_payment_method_widgets(self):
        if not self._edit_mode:
            return
        group = IPaymentGroup(self.model, connection=self.conn)
        if not group:
            raise ValueError('You should have a IPaymentGroup facet '
                             'defined at this point')
        if group.default_method == METHOD_MONEY:
            self.pm_slave.cash_check.set_active(True)
        elif group.default_method == METHOD_GIFT_CERTIFICATE:
            self.pm_slave.certificate_check.set_active(True)
        else:
            self.pm_slave.othermethods_check.set_active(True)

    def update_totals(self):
        for field_name in ('total_sale_amount', 'sale_subtotal'):
            self.proxy.update(field_name)

    def setup_combo(self):
        salespersons = Person.iselect(ISalesPerson, connection=self.conn)
        items = [(s.get_adapted().name, s) for s in salespersons]
        self.salesperson_combo.prefill(items)

    def _get_selected_payment_method(self):
        if (isinstance(self.pm_slave, SelectCashMethodSlave)
            or self.pm_slave.cash_check.get_active()):
            return IMoneyPM
        elif self.pm_slave.certificate_check.get_active():
            return IGiftCertificatePM
        else:
            return IMultiplePM

    def _update_next_button(self, slave, method_iface):
        if method_iface is IMoneyPM:
            self.wizard.enable_finish()
        elif (method_iface is IGiftCertificatePM
              or method_iface is IMultiplePM):
            self.wizard.disable_finish()
        else:
            raise ValueError("Invalid payment method interface, got %s"
                             % method_iface)

    #
    # WizardStep hooks
    #

    def post_init(self):
        self.salesperson_combo.grab_focus()
        if self._get_selected_payment_method() is IMoneyPM:
            self.wizard.enable_finish()

    def next_step(self):
        step_class = None
        selected_method = self._get_selected_payment_method()
        group = self.wizard.get_payment_group()
        if selected_method is IMoneyPM:
            group.default_method = METHOD_MONEY
            self.wizard.setup_cash_payment()
        elif selected_method is IGiftCertificatePM:
            table = GiftCertificate.getAdapterClass(ISellable)
            if not table.get_sold_sellables(self.conn).count():
                msg = _('There is no sold gift certificates at this moment.'
                        '\nPlease select another payment method.')
                notify_dialog(msg, title=_('Gift Certificate Error'))
                return self
            step_class = GiftCertificateSelectionStep
            group.default_method = METHOD_GIFT_CERTIFICATE
        else:
            group.default_method = METHOD_MULTIPLE
            if not self.wizard.skip_payment_step:
                step_class = PaymentMethodStep
        if step_class is None:
            # Return None here means call wizard.finish, which is exactly
            # what we need
            return
        return step_class(self.wizard, self, self.conn, self.model)

    #
    # BaseEditorSlave hooks
    #

    def setup_slaves(self):
        self.disccharge_slave = DiscountChargeSlave(self.conn, self.model,
                                                    self.model_type)
        self.disccharge_slave.connect('discount-changed',
                                      self.on_disccharge_slave_changed)
        slave_holder = 'discount_charge_slave'
        if self.get_slave(slave_holder):
            self.detach_slave(slave_holder)
        self.attach_slave('discount_charge_slave', self.disccharge_slave)
        pm_ifaces = get_active_pm_ifaces()
        if len(pm_ifaces) == 1:
            self.pm_slave = SelectCashMethodSlave()
        else:
            self.pm_slave = SelectPaymentMethodSlave(pm_ifaces)
            self._setup_payment_method_widgets()
        self.pm_slave.connect('method-changed', self._update_next_button)
        self.attach_slave('select_method_holder', self.pm_slave)

    def setup_proxies(self):
        self.setup_combo()
        if not sysparam(self.conn).ACCEPT_CHANGE_SALESPERSON:
            self.salesperson_combo.set_sensitive(False)
        self.proxy = self.add_proxy(self.model,
                                    SalesPersonStep.proxy_widgets)

    #
    # Callbacks
    #

    def on_disccharge_slave_changed(self, slave):
        self.update_totals()

    def on_notes_button__clicked(self, *args):
        run_dialog(NoteEditor, self, self.conn, self.model, 'notes',
                   title=_("Additional Information"))


#
# Main wizard
#


class SaleWizard(BaseWizard):
    size = (600, 400)

    def __init__(self, conn, model, title=_('Sale Checkout'),
                 edit_mode=False, skip_payment_step=False):
        self.title = title
        self.skip_payment_step = skip_payment_step
        self.gift_certificates = []
        first_step = SalesPersonStep(self, conn, model, edit_mode)
        BaseWizard.__init__(self, conn, first_step, model,
                            edit_mode=edit_mode)
        group = self.get_payment_group()
        if not self.edit_mode:
            group.clear_preview_payments()

    #
    # WizardStep hooks
    #

    def finish(self):
        if self.gift_certificates:
            group = self.get_payment_group()
            for certificate in self.gift_certificates:
                certificate.group = group
        if self.edit_mode or not sysparam(self.conn).CONFIRM_SALES_ON_TILL:
            self.model.confirm_sale()
            print_cheques_for_payment_group(self.conn,
                                            self.get_payment_group())
        else:
            self.model.validate()
            # TODO We should here update the stocks and mark them as
            # reserved for this sale. Lets do it in another bug
        self.retval = True
        self.close()

    #
    # Auxiliar methods
    #

    def get_payment_group(self):
        group = IPaymentGroup(self.model, connection=self.conn)
        if not group:
            group = self.model.addFacet(IPaymentGroup,
                                        connection=self.conn)
        return group

    def setup_cash_payment(self, total=None):
        money_method = sysparam(self.conn).METHOD_MONEY
        group = self.get_payment_group()
        total = total or group.get_total_received()
        money_method.setup_inpayments(total, group)
