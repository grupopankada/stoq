# -*- coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2012 Async Open Source <http://www.async.com.br>
## All rights reserved
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., or visit: http://www.gnu.org/.
##
## Author(s): Stoq Team <stoq-devel@async.com.br>
##

import mock
from stoqlib.database.settings import DatabaseSettings
from stoqlib.gui.uitestutils import GUITest

from stoq.gui.config import FirstTimeConfigWizard


class _MockConfig:
    def __init__(self, settings):
        self.settings = settings
        self.options = None

    def get_settings(self):
        return self.settings

    def set_from_options(self, options):
        self.options = options

    def get_password(self):
        pass

    def load_settings(self, settings):
        pass

    def get(self, section, value):
        if (section, value) == ('Database', 'enable_production'):
            return True
        raise AssertionError((section, value))


class TestConfirmSaleWizard(GUITest):
    @mock.patch('stoq.gui.config.ProcessView.execute_command')
    @mock.patch('stoq.gui.config.ensure_admin_user')
    def testLocal(self, ensure_admin_user, execute_command):
        options = mock.Mock()
        options.sqldebug = False
        options.verbose = False

        settings = DatabaseSettings(address='localhost',
                                    port=12345,
                                    dbname='dbname',
                                    username='username',
                                    password='password')
        config = _MockConfig(settings)
        wizard = FirstTimeConfigWizard(options, config)

        self.check_wizard(wizard, 'wizard-config-welcome')
        self.click(wizard.next_button)

        step = wizard.get_current_step()
        self.assertTrue(step.radio_local.get_active())
        self.check_wizard(wizard, 'wizard-config-database-location')
        self.click(wizard.next_button)

        step = wizard.get_current_step()
        self.assertTrue(step.empty_database_radio.get_active())
        self.check_wizard(wizard, 'wizard-config-installation-mode')
        self.click(wizard.next_button)

        self.check_wizard(wizard, 'wizard-config-plugins')
        self.click(wizard.next_button)

        step = wizard.get_current_step()
        step.name.update('Name')
        step.email.update('example@example.com')
        step.phone.update('1212341234')
        wizard.tef_request_done = True
        self.check_wizard(wizard, 'wizard-config-tef')
        self.click(wizard.next_button)

        step = wizard.get_current_step()
        step.password_slave.password.update('foobar')
        step.password_slave.confirm_password.update('foobar')
        self.check_wizard(wizard, 'wizard-config-admin-password')
        self.click(wizard.next_button)

        self.check_wizard(wizard, 'wizard-config-installing')
        execute_command.assert_called_once_with([
            'stoqdbadmin', 'init',
            '--no-load-config', '--no-register-station', '-v',
            '--enable-plugins', 'ecf',
            '--create-dbuser',
            '-d', 'stoq',
            '-H', '/var/run/postgresql',
            '-p', '5432',
            '-u', 'username',
            '-w', 'password'])

        step = wizard.get_current_step()
        step.process_view.emit('finished', 0)
        self.click(wizard.next_button)

        self.check_wizard(wizard, 'wizard-config-done')
