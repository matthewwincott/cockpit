#!/usr/bin/env python
# -*- coding: utf-8 -*-

## Copyright (C) 2021 University of Oxford
##
## This file is part of Cockpit.
##
## Cockpit is free software: you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation, either version 3 of the License, or
## (at your option) any later version.
##
## Cockpit is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with Cockpit.  If not, see <http://www.gnu.org/licenses/>.

import typing
import wx
import Pyro4
import numpy
import aurox_clarity.processor
from microscope.misc_devices.aurox import ClarityDiskSectioning
from cockpit import depot
from cockpit import events
from cockpit.devices import microscopeDevice, microscopeCamera
from cockpit.gui import CockpitEvent, EVT_COCKPIT, EvtEmitter
from cockpit.gui.camera.window import getImageForCamera
from cockpit.gui.device import EnableButton
from cockpit.handlers.deviceHandler import DeviceHandler, STATES

# Period of the timer used to update the user interface
TIMER_PERIOD_MS = 1000
# Priority number for the sectioning post-processor
PRIORITY_SECTIONING = 100


class Clarity(microscopeDevice.MicroscopeBase):
    def __init__(self, *args, **kwargs):
        """Custom panel for Aurox Clarity devices.

        The configuration expects two keys:

            filter cubes: a list of labels, separated by new lines, for each
            turret position. Four labels must be defined and empty lines are
            skipped. It is recommended to use "Empty" or "None" labels for the
            positions without a filter cube.

            camera: the name of the camera associated with this instrument.

        Example configuration:

            [Aurox Clarity]
            type: cockpit.devices.aurox.Clarity
            uri: PYRO:AuroxClarity@127.0.0.1:8000
            filter cubes:
                DAPI
                GFP
                dsRed
                Cy5
            camera: Prime BSI
        """
        super().__init__(*args, **kwargs)
        # Store references to some of the widgets
        self._panel_inner = None
        self._choice_sect = None
        self._choice_fcube = None
        self._cbox_calib = None
        self._button_calib_light = None
        self._button_section = None
        self._cbox_door = None
        self._timer = None
        # Private attributes
        self._processors = [None] * 4
        # Check if the expected keys are present
        for key in ("filter cubes", "camera"):
            if self.config.get(key) is None:
                raise Exception(
                    "Missing '%s' key in configuration of device '%s'."
                    % (key, self.name)
                )
        # Parse filter cubes config
        self._filter_cubes = [
            label
            for label in self.config.get("filter cubes").split("\n")
            if label
        ]
        if len(self._filter_cubes) != 4:
            raise Exception(
                "Wrong value of key 'filter cubes' of device '%s'. "
                "Expected a list of four labels, each on a new line."
                % self.name
            )
        # Store camera name
        self._camera_name = self.config.get("camera")

    def getHandlers(self) -> typing.Tuple[DeviceHandler]:
        self.handlers = (
            DeviceHandler(
                self.name,
                "Aurox Clarity",
                False,
                {
                    "getIsEnabled": self._proxy.get_is_enabled,
                    "setEnabled": self._toggle_state,
                },
                depot.GENERIC_DEVICE,
            ),
        )
        return self.handlers

    def makeUI(self, parent: wx.Window) -> wx.Window:
        # Create panels and sizers
        panel_outer = wx.Panel(parent, style=wx.BORDER_RAISED)
        self._panel_inner = wx.Panel(panel_outer)
        panel_outer_sizer = wx.BoxSizer(wx.VERTICAL)
        panel_inner_sizer = wx.BoxSizer(wx.VERTICAL)
        # Add serial number
        panel_outer_sizer.Add(
            wx.StaticText(
                panel_outer,
                label="S/N: " + str(self._proxy.get_id()),
                style=wx.ALIGN_CENTRE_HORIZONTAL,
            ),
            0,
            wx.EXPAND | wx.BOTTOM,
            4,
        )
        # Add enable button
        button_enable = EnableButton(panel_outer, self.handlers[0])
        button_enable.SetLabel("Enable")
        button_enable.manageStateOf(self._panel_inner)
        panel_outer_sizer.Add(button_enable, 0, wx.EXPAND | wx.BOTTOM, 4)
        # Add inner panel to outer; need to happen after the enable button
        panel_outer_sizer.Add(self._panel_inner, 1, wx.EXPAND)
        # Sectioning widget
        panel_inner_sizer.Add(
            wx.StaticText(self._panel_inner, label="Sectioning:"),
            0,
            wx.BOTTOM,
            4,
        )
        self._choice_sect = wx.Choice(self._panel_inner)
        self._choice_sect.Set(
            [member.name for member in list(ClarityDiskSectioning)]
        )
        self._choice_sect.Bind(
            wx.EVT_CHOICE,
            lambda evt: self._set_disk_position_async(
                ClarityDiskSectioning(evt.Selection)
            ),
        )
        panel_inner_sizer.Add(self._choice_sect, 0, wx.EXPAND | wx.BOTTOM, 4)
        # Filters widget
        panel_inner_sizer.Add(
            wx.StaticText(self._panel_inner, label="Filter cube:"),
            0,
            wx.BOTTOM,
            4,
        )
        self._choice_fcube = wx.Choice(self._panel_inner)
        self._choice_fcube.Set(self._filter_cubes)
        self._choice_fcube.Bind(
            wx.EVT_CHOICE,
            lambda evt: self._set_filter_position_async(evt.Selection),
        )
        panel_inner_sizer.Add(self._choice_fcube, 0, wx.EXPAND | wx.BOTTOM, 4)
        # Calibration status
        calibration_sizer = wx.BoxSizer(wx.HORIZONTAL)
        calibration_sizer.Add(
            wx.StaticText(self._panel_inner, label="Channel calibrated:"),
            1,
            wx.EXPAND | wx.RIGHT,
            4,
        )
        self._cbox_calib = wx.CheckBox(self._panel_inner)
        self._cbox_calib.Disable()
        calibration_sizer.Add(self._cbox_calib, 0, wx.EXPAND)
        panel_inner_sizer.Add(calibration_sizer, 0, wx.EXPAND | wx.BOTTOM, 4)
        # Calibration light
        self._button_calib_light = wx.ToggleButton(
            self._panel_inner, label="Toggle calibration LED"
        )
        self._button_calib_light.Bind(
            wx.EVT_TOGGLEBUTTON,
            lambda e: setattr(
                self._proxy, "calibration_led", e.GetEventObject().GetValue()
            ),
        )
        panel_inner_sizer.Add(
            self._button_calib_light, 0, wx.EXPAND | wx.BOTTOM, 4
        )
        # Calibration
        button_calib = wx.Button(self._panel_inner, label="Calibrate")
        button_deforms = wx.Button(self._panel_inner, label="Save deforms")
        button_calib.Bind(wx.EVT_BUTTON, self._on_calib)
        button_deforms.Bind(wx.EVT_BUTTON, self._on_deforms)
        panel_inner_sizer.Add(button_calib, 0, wx.EXPAND | wx.BOTTOM, 4)
        panel_inner_sizer.Add(button_deforms, 0, wx.EXPAND | wx.BOTTOM, 4)
        # Sectioning mode
        self._button_section = wx.ToggleButton(
            self._panel_inner, label="Toggle sectioning mode"
        )
        self._button_section.Bind(wx.EVT_TOGGLEBUTTON, self._on_section)
        panel_inner_sizer.Add(
            self._button_section, 0, wx.EXPAND | wx.BOTTOM, 4
        )
        # Door open
        door_sizer = wx.BoxSizer(wx.HORIZONTAL)
        door_sizer.Add(
            wx.StaticText(self._panel_inner, label="Door closed:"),
            1,
            wx.EXPAND | wx.RIGHT,
            4,
        )
        self._cbox_door = wx.CheckBox(self._panel_inner)
        self._cbox_door.Disable()
        door_sizer.Add(self._cbox_door, 0, wx.EXPAND)
        panel_inner_sizer.Add(door_sizer, 0, wx.EXPAND)
        # Configure sizers
        self._panel_inner.SetSizerAndFit(panel_inner_sizer)
        panel_outer.SetSizerAndFit(panel_outer_sizer)
        # Initialise and configure the timer
        self._timer = wx.Timer(self._panel_inner)
        self._panel_inner.Bind(wx.EVT_TIMER, lambda _: self._update_UI())
        listener = EvtEmitter(button_enable, events.DEVICE_STATUS)
        listener.Bind(EVT_COCKPIT, self._timer_controller)
        # Update the widgets
        self._update_UI()
        # Save reference to outer panel before returning it
        self.panel = panel_outer
        return panel_outer

    def _toggle_state(self, state: bool) -> None:
        """Toggle the state of the Clarity device and update the UI."""
        if state:
            self._proxy.enable()
        else:
            self._proxy.disable()
        self._update_UI()

    def _set_filter_position_async(self, position: int) -> None:
        asproxy = Pyro4.Proxy(self.uri)
        asproxy._pyroAsync()
        asproxy.set_filter_position(position)

    def _set_disk_position_async(
        self, position: ClarityDiskSectioning
    ) -> None:
        asproxy = Pyro4.Proxy(self.uri)
        asproxy._pyroAsync()
        asproxy.set_disk_position(position)

    def _update_UI(self) -> None:
        """Query the Clarity device and update the UI widgets accordingly."""
        # The enable button is automatically updated when a DEVICE_STATUS
        # event is published to its handler
        try:
            state = STATES.disabled
            if self._proxy.get_is_enabled():
                state = STATES.enabled
            if (
                self._proxy.get_disk_position() is None
                or self._proxy.get_filter_position() is None
                or not self._proxy.door_closed
            ):
                state = STATES.busy
            events.publish(events.DEVICE_STATUS, self.handlers[0], state)
        except Exception:
            events.publish(
                events.DEVICE_STATUS, self.handlers[0], STATES.error
            )
            return
        # Disk position
        dpos = self._proxy.get_disk_position()
        if dpos is not None and not self._choice_sect.HasFocus():
            self._choice_sect.SetSelection(dpos)
        # Filter position
        fpos = self._proxy.get_filter_position()
        if fpos is not None and not self._choice_fcube.HasFocus():
            self._choice_fcube.SetSelection(fpos)
        # Calibration
        calibrated = fpos is not None and self._processors[fpos] is not None
        self._cbox_calib.SetValue(calibrated)
        self._button_calib_light.SetValue(self._proxy.calibration_led)
        # Sectioning
        if not calibrated and self._button_section.GetValue():
            # The filter cube turret has moved, while the button is toggled, to
            # a position which is not calibrated
            self._button_section.SetValue(False)
            # SetValue() does not emit event, so it has to be done manually
            evt = wx.CommandEvent(wx.EVT_TOGGLEBUTTON.typeId)
            evt.SetEventObject(self._button_section)
            self._button_section.QueueEvent(evt)
        self._button_section.Enable(calibrated)
        # Door
        self._cbox_door.SetValue(self._proxy.door_closed)

    def _section_data(self, data: numpy.ndarray) -> numpy.ndarray:
        """Perform sectioning routine."""
        # Ensure the filter cube turret is not moving
        fpos = self._proxy.get_filter_position()
        if fpos is not None:
            data = self._processors[fpos].process(data).get()
        return data

    def _on_calib(self, _: CockpitEvent) -> None:
        """Perform calibration."""
        # Get the camera's handler
        camera_handler = depot.getHandlerWithName(self._camera_name)
        if camera_handler is None:
            raise Exception(
                "Couldn't find camera handler with name '%s'."
                % self._camera_name
            )
        # Get the camera's last image data
        data = getImageForCamera(camera_handler)
        if data is None:
            raise Exception("No image data found for the associated camera.")
        # Set the status to BUSY while the calibration routine is executing
        events.publish(events.DEVICE_STATUS, self.handlers[0], STATES.busy)
        # Ensure the filter cube turret is not moving
        fpos = self._proxy.get_filter_position()
        while fpos is None:
            fpos = self._proxy.get_filter_position()
        # Create the processor instance
        self._processors[fpos] = aurox_clarity.processor.Processor(data)
        # Restore the status
        events.publish(events.DEVICE_STATUS, self.handlers[0], STATES.enabled)

    def _on_section(self, e: CockpitEvent) -> None:
        """Toggle the sectioning mode."""
        button_toggled = e.GetEventObject().GetValue()
        camera_device = depot.getDeviceWithName(self._camera_name)
        if camera_device is None:
            raise Exception(
                "Couldn't find camera device with name '%s'."
                % self._camera_name
            )
        if button_toggled:
            camera_device.addPostProcessor(
                PRIORITY_SECTIONING,
                self._section_data,
                lambda shape: (shape[0] // 2, shape[1]),
            )
        else:
            camera_device.removePostProcessor(
                PRIORITY_SECTIONING, self._section_data
            )

    def _on_deforms(self, _: CockpitEvent) -> None:
        """Save deformation maps to a compressed Numpy archive file."""
        # Create a mapping of the form
        # {<filter label>: <tuple of deformation maps>}
        deforms = {
            self._filter_cubes[i]: p.get_deforms()
            for i, p in enumerate(self._processors)
            if p
        }
        if not deforms:
            raise Exception(
                "No deformation maps found. Ensure at least one of the "
                "channels is calibrated."
            )
        # Ask the user to select path and save the data on success
        with wx.FileDialog(
            self._panel_inner,
            message="Save file as ...",
            wildcard="Numpy archive files (*.npz)|*.npz",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as fdiag:
            if fdiag.ShowModal() == wx.ID_CANCEL:
                raise Exception("Saving of deformation maps cancelled!")
            path = fdiag.GetPath()
            numpy.savez_compressed(path, **deforms)

    def _timer_controller(self, e: CockpitEvent) -> None:
        """Callback for the enable button, starts or stops the timer."""
        if e.EventData[0] != self.handlers[0]:
            # The Cockpit status event came from an unrelated device
            return
        if e.EventData[1] == STATES.disabled:
            self._timer.Stop()
        elif not self._timer.IsRunning():
            # Start the timer only if it is not running already,
            # otherwise it will be paused and re-started, creating a delay
            self._timer.Start(TIMER_PERIOD_MS)
