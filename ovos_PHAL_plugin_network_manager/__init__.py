from os.path import join, dirname

import pexpect
import random
import subprocess
from mycroft_bus_client.message import Message, dig_for_message
from ovos_plugin_manager.phal import PHALPlugin
from ovos_utils import create_daemon
from ovos_utils.enclosure.api import EnclosureAPI
from ovos_utils.gui import is_gui_connected
from ovos_utils.device_input import can_use_touch_mouse
from ovos_utils.log import LOG
from ovos_utils.network_utils import is_connected
from time import sleep


class NetworkManagerPlugin(PHALPlugin):

    def __init__(self, bus=None, config=None):
        super().__init__(bus=bus, name="ovos-PHAL-plugin-network-manager", config=config)
        self.monitoring = False
        self.in_setup = False
        self.connected = False
        self.time_between_checks = 30
        self.mycroft_ready = False
        self.stop_on_internet = False
        self.timeout_after_internet = 90
        self.active_client = None

        self.enclosure = EnclosureAPI(bus=self.bus, skill_id=self.name)
        self.start_internet_check()

        # Register Generic Client Bus Events
        self.bus.on("ovos.phal.nm.client.mode.select.gui", self.handle_mode_select_gui_client)
        self.bus.on("ovos.phal.nm.client.mode.select.balena", self.handle_mode_select_balena_client)
        self.bus.on("ovos.phal.nm.set.active.client", self.handle_set_active_client)
        self.bus.on("ovos.phal.nm.remove.active.client", self.handle_remove_active_client)

        # Register Network Manager Events (Not used by balena as it manages its own connection)
        # The gui client relies on this to connect

        self.bus.on("ovos.phal.nm.connect", self.handle_network_connect_request)
        self.bus.on("ovos.phal.nm.disconnect", self.handle_network_disconnect_request)
        self.bus.on("ovos.phal.nm.forget", self.handle_network_forget_request)
        self.bus.on("ovos.phal.nm.get.connected", self.handle_network_connected_query)

    def start_internet_check(self):
        create_daemon(self._watchdog)

    def stop_internet_check(self):
        self.monitoring = False

    def _watchdog(self):
        try:
            self.monitoring = True
            LOG.info("Wifi watchdog started")
            output = subprocess.check_output("nmcli connection show",
                                             shell=True).decode("utf-8")
            if "wifi" in output:
                LOG.info("Detected previously configured wifi, starting "
                         "grace period to allow it to connect")
                sleep(self.grace_period)
            while self.monitoring:
                if self.in_setup:
                    sleep(1)  # let setup do it's thing
                    continue

                if not is_connected():
                    LOG.info("NO INTERNET")
                    if not self.is_connected_to_wifi():
                        LOG.info("LAUNCH SETUP")
                        try:
                            self.launch_networking_setup()  # blocking
                        except Exception as e:
                            LOG.exception(e)
                    else:
                        LOG.warning("CONNECTED TO WIFI, BUT NO INTERNET!!")

                sleep(self.time_between_checks)
        except Exception as e:
            LOG.error("Wifi watchdog crashed unexpectedly")
            LOG.exception(e)

    # wifi setup
    @staticmethod
    def get_wifi_ssid():
        SSID = None
        try:
            SSID = subprocess.check_output(["iwgetid", "-r"]).strip()
        except subprocess.CalledProcessError:
            # If there is no connection subprocess throws a 'CalledProcessError'
            pass
        return SSID

    @staticmethod
    def is_connected_to_wifi():
        return NetworkManagerPlugin.get_wifi_ssid() is not None

    def launch_networking_setup(self):
        if not self.in_setup:
            self.bus.emit(Message("ovos.wifi.setup.started"))
        self.in_setup = True

        try:
            if is_gui_connected(self.bus) and can_use_touch_mouse():
                self.bus.emit("ovos.phal.nm.client.mode.selector")
            else:
                self.bus.emit("ovos.phal.nm.activate.balena.client")

        except Exception as e:
            LOG.exception(e)

    def handle_mode_select_gui_client(self, message=None):
        self.bus.emit("ovos.phal.nm.activate.gui.client")

    def handle_mode_select_balena_client(self, message=None):
        self.bus.emit("ovos.phal.nm.activate.balena.client")

    def handle_set_active_client(self, message=None):
        set_client = message.data.get("client", "")
        self.active_client = set_client

    def handle_remove_active_client(self, message=None):
        if self.active_client == "ovos-PHAL-plugin-gui-network-client":
            self.bus.emit("ovos.phal.nm.deactivate.gui.client")
        if self.active_client == "ovos-PHAL-plugin-balena-wifi":
            self.bus.emit("ovos.phal.nm.deactivate.balena.client")

        self.active_client = ""

    # bus events
    def handle_internet_connected(self, message=None):
        """System came online later after booting."""
        self.enclosure.mouth_reset()
        # sync clock as soon as we have internet
        self.bus.emit(Message("system.ntp.sync"))
        self.stop_setup()  # just in case

    ### Network Manager Events

    def handle_network_connect_request(self, message):
        network_name = message.data.get("connection_name", "")
        secret_phrase = message.data.get("password", "")
        security_type = message.data.get("security_type", "")

        if secret_phrase is not None:
            # Connection requires password
            # Use subprocess or similar to call nmcli and handle connection
            LOG.info("Connecting via nmcli to secure network")
        else:
            # Connection is open
            # Use subprocess or similar to call nmcli and handle connection
            LOG.info("Connecting via nmcli to open network")

    def handle_network_disconnect_request(self, message):
        network_name = message.data.get("connection_name", "")
        # Use subprocess or similar to call nmcli and handle connection
        LOG.info("Disconnect via nmcli")

    def handle_network_forget_request(self, message):
        network_name = message.data.get("connection_name", "")
        # Use subprocess or similar to call nmcli and handle connection
        LOG.info("Forget Network via nmcli")

    def handle_network_connected_query(self, message):
        network_connection_name = NetworkManagerPlugin.get_wifi_ssid()
        if network_connection_name is not None:
            self.bus.emit("ovos.phal.nm.is.connected", {"connection_name": network_connection_name})

    # cleanup
    def stop_setup(self):
        if self.active_client == "ovos-PHAL-plugin-gui-network-client":
            self.bus.emit("ovos.phal.nm.cleanup.gui.client")
        else if self.active_client == "ovos-PHAL-plugin-balena-wifi":
            self.bus.emit("ovos.phal.nm.cleanup.balena.client")

        self.in_setup = False

    def shutdown(self):
        self.monitoring = False
        self.bus.remove("mycroft.internet.connected", self.handle_internet_connected)
        self.stop_setup()
        super().shutdown()
