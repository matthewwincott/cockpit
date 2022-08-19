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


"""Cameras from Python Microscope device server."""

import decimal
import Pyro4
import wx
import dataclasses
import collections.abc
import typing

from cockpit import depot
import numpy as np
from cockpit import events
import cockpit.gui.device
import cockpit.gui.guiUtils
import cockpit.handlers.camera
import cockpit.util.listener
import cockpit.util.logger
import cockpit.util.threads
import cockpit.util.userConfig
from cockpit.devices.microscopeDevice import MicroscopeBase
from cockpit.devices.camera import CameraDevice
from cockpit.handlers.objective import ObjectiveHandler
from cockpit.interfaces.imager import pauseVideo
from microscope.devices import ROI, Binning
from microscope import TriggerMode, TriggerType

# Pseudo-enum to track whether device defaults in place.
(DEFAULTS_NONE, DEFAULTS_PENDING, DEFAULTS_SENT) = range(3)


@dataclasses.dataclass
class _PostProcessor:
    """Encapsulation for all the data associated with post-processing units.

    Args:
        priority: priority of the post-processor
        f_data: post-processor itself
        f_shape: expected shape of the output data of the post-processor, in
        the format (width, height).
    """
    priority: int
    f_data: collections.abc.Callable[np.ndarray, np.ndarray]
    f_shape: collections.abc.Callable[tuple[int, int], tuple[int, int]]


class MicroscopeCamera(MicroscopeBase, CameraDevice):
    """A class to control remote python microscope cameras."""
    def __init__(self, name, config):
        # camConfig is a dict with containing configuration parameters.
        super().__init__(name, config)
        self.enabled = False
        self.panel = None
        self.modes = []
        self._post_processors = []

    def initialize(self):
        # Parent class will connect to proxy
        super().initialize()
        # Lister to receive data
        self.listener = cockpit.util.listener.Listener(self._proxy,
                                               lambda *args: self.receiveData(*args))
        try:
            self.updateSettings()
        except:
            pass
        if 'readout mode' in self.settings:
            self.modes = self.describe_setting('readout mode')['values']
        else:
            self.modes = []
        if self.baseTransform:
            self._setTransform(self.baseTransform)

    @property
    def _modenames(self):
        # Modes are a descriptive string of the form
        # [amp-type] [freq] [channel]
        if not self.modes:
            return ['default']
        import re
        channels = set()
        chre = re.compile(r' CH([0-9]+)', re.IGNORECASE)
        ampre = re.compile(r'CONVENTIONAL ', re.IGNORECASE)
        modes = []
        for i, m in self.modes:
            modes.append(ampre.sub('CONV ', m))
            match = chre.search(m)
            if match:
                channels.union(match.groups())

        if len(channels) < 2:
            modes = [chre.sub('', m) for m in modes]
        return modes

    def finalizeInitialization(self):
        super().finalizeInitialization()
        self._readUserConfig()
        # Decorate updateSettings. Can't do this from the outset, as camera
        # is initialized before interfaces.imager.
        self.updateSettings = pauseVideo(self.updateSettings)


    def updateSettings(self, settings=None):
        if settings is not None:
            self._proxy.update_settings(settings)
        self.settings.update(self._proxy.get_all_settings())
        events.publish(events.SETTINGS_CHANGED % str(self))


    def _setTransform(self, tr):
        self._proxy.set_transform(tr)
        self.updateSettings()


    def cleanupAfterExperiment(self):
        """Clean-up actions after an experiment has ended."""
        # Ensure the camera is ready to expose
        time.sleep(self.getTimeBetweenExposures(self.name) / 1000)
        # Restore settings as they were prior to experiment.
        if self.enabled:
            self.updateSettings(self.cached_settings)
            #self._proxy.update_settings(self.settings)
            self._proxy.enable()
        self.handler.exposureMode = self._getCockpitExposureMode()


    def performSubscriptions(self):
        """Perform subscriptions for this camera."""
        events.subscribe(events.CLEANUP_AFTER_EXPERIMENT,
                self.cleanupAfterExperiment)
        events.subscribe('objective change',
                self.onObjectiveChange)


    def onObjectiveChange(self, handler: ObjectiveHandler) -> None:
        # Changing an objective might change the transform since a
        # different objective might actually mean a different light
        # path (see comments on issue #456).
        self.updateTransform(handler.transform)


    def setAnyDefaults(self):
        # Set any defaults found in userConfig.
        # TODO - migrate defaults to a universalDevice base class.
        if self.defaults != DEFAULTS_PENDING:
            # notrhing to do
            return
        try:
            self._proxy.update_settings(self.settings)
        except Exception as e:
            print (e)
        else:
            self.defaults = DEFAULTS_SENT


    def _readUserConfig(self):
        idstr = self.handler.getIdentifier() + '_SETTINGS'
        defaults = cockpit.util.userConfig.getValue(idstr)
        if defaults is None:
            self.defaults = DEFAULTS_NONE
            return
        self.updateSettings(defaults)
        self.defaults = DEFAULTS_PENDING
        self.setAnyDefaults()

    def _getCockpitExposureMode(self) -> int:
        # Cockpit does not support all possible comabinations of
        # trigger type and mode from Microscope.
        microscope_trigger_to_cockpit_exposure = {
            (
                TriggerType.SOFTWARE,
                TriggerMode.ONCE,
            ): cockpit.handlers.camera.TRIGGER_SOFT,
            (
                TriggerType.HIGH,
                TriggerMode.ONCE,
            ): cockpit.handlers.camera.TRIGGER_BEFORE,
            (
                TriggerType.LOW,
                TriggerMode.ONCE
            ): cockpit.handlers.camera.TRIGGER_AFTER,
            (
                TriggerType.HIGH,
                TriggerMode.BULB,
            ): cockpit.handlers.camera.TRIGGER_DURATION,
        }
        return microscope_trigger_to_cockpit_exposure[
            (self._proxy.trigger_type, self._proxy.trigger_mode)
        ]

    def getHandlers(self):
        """Return camera handlers."""
        trigsource = self.config.get('triggersource', None)
        trigline = self.config.get('triggerline', None)
        if trigsource:
            trighandler = depot.getHandler(trigsource, depot.EXECUTOR)
        else:
            trighandler = None

        self.handler = cockpit.handlers.camera.CameraHandler(
                "%s" % self.name, "universal camera",
                {'setEnabled': self.enableCamera,
                 'getImageSize': self.getImageSize,
                 'getTimeBetweenExposures': self.getTimeBetweenExposures,
                 'prepareForExperiment': self.prepareForExperiment,
                 'getExposureTime': self.getExposureTime,
                 'setExposureTime': self.setExposureTime,
                 'getExposureTime': self.getExposureTime,
                 'setROI': self.setROI,
                 'getROI': self.getROI,
                 'getSensorShape': self.getSensorShape,
                 'makeUI': self.makeUI,
                 'softTrigger': self.softTrigger},
            cockpit.handlers.camera.TRIGGER_SOFT,
            trighandler,
            trigline)

        return [self.handler]


    @pauseVideo
    def enableCamera(self, name, shouldEnable):
        """Enable the hardware."""
        if not shouldEnable:
            # Disable the camera, if it is enabled.
            if self.enabled:
                self.enabled = False
                self._proxy.disable()
                self.listener.disconnect()
                return self.enabled

        # Enable the camera
        if self.enabled:
            # Nothing to do.
            return
        self.setAnyDefaults()
        # Use async call to allow hardware time to respond.
        # Pyro4.async API changed - now modifies original rather than returning
        # a copy. This workaround from Pyro4 maintainer.
        asproxy = Pyro4.Proxy(self._proxy._pyroUri)
        asproxy._pyroAsync()
        result = asproxy.enable()
        result.wait(timeout=10)
        self.enabled = self._proxy.get_is_enabled()
        if self.enabled:
            self.handler.exposureMode = self._getCockpitExposureMode()
            self.listener.connect()
        self.updateSettings()
        return self.enabled


    def getExposureTime(self, name=None, isExact=False):
        """Read the real exposure time from the camera."""
        # Camera uses times in s; cockpit uses ms.
        t = self._proxy.get_exposure_time()
        if isExact:
            return decimal.Decimal(t) * (decimal.Decimal(1000.0))
        else:
            return t * 1000.0


    def getImageSize(self, name):
        """Read the image size from the camera."""
        roi = self.getROI(name)
        binning = self._proxy.get_binning()
        if not isinstance(binning, Binning):
            cockpit.util.logger.log.warning("%s returned tuple not Binning()" % self.name)
            binning = Binning(*binning)
        size = (roi.width//binning.h, roi.height//binning.v)
        for pp in self._post_processors:
            size = pp.f_shape(size)
        return size

    def getROI(self, name):
        """Read the ROI from the camera"""
        roi = self._proxy.get_roi()
        if not isinstance(roi, ROI):
            cockpit.util.logger.log.warning("%s returned tuple not ROI()" % self.name)
            roi = ROI(*roi)
        return roi

    def getSensorShape(self, name):
        """Read the sensor shape from the camera"""
        sensor_shape = self._proxy.get_sensor_shape()
        return sensor_shape

    def getSavefileInfo(self, name):
        """Return an info string describing the measurement."""
        #return "%s: %s image" % (name, self.imageSize)
        return ""


    def getTimeBetweenExposures(self, name, isExact=False):
        """Get the amount of time between exposures.

        This is the time that must pass after stopping one exposure
        before another can be started, in milliseconds."""
        # Camera uses time in s; cockpit uses ms.
        #Note cycle time is exposure+Readout!
        t_cyc = self._proxy.get_cycle_time() * 1000.0
        t_exp = self._proxy.get_exposure_time() * 1000.0
        t = t_cyc - t_exp
        if isExact:
            result = decimal.Decimal(t)
        else:
            result = t
        return result


    def prepareForExperiment(self, name, experiment):
        """Make the hardware ready for an experiment."""
        self.cached_settings.update(self.settings)


    def receiveData(self, *args):
        """This function is called when data is received from the hardware."""
        (image, timestamp) = args
        if not isinstance(image, Exception):
            for pp in self._post_processors:
                image = pp.f_data(image)
            events.publish(events.NEW_IMAGE % self.name, image, timestamp)
        else:
            # Handle the dropped frame by publishing an empty image of the correct
            # size. Use the handler to fetch the size, as this will use a cached value,
            # if available.
            events.publish(events.NEW_IMAGE % self.name,
                           np.zeros(self.handler.getImageSize(), dtype=np.int16),
                           timestamp)
            raise image


    def setExposureTime(self, name, exposureTime):
        """Set the exposure time."""
        # Camera uses times in s; cockpit uses ms.
        self._proxy.set_exposure_time(exposureTime / 1000.0)


    def setROI(self, name, roi):
        result = self._proxy.set_roi(roi)

        if not result:
            cockpit.util.logger.log.warning("%s could not set ROI" % self.name)
        
    def softTrigger(self, name=None):
        if self.enabled:
            self._proxy.soft_trigger()


    ### UI functions ###
    def makeUI(self, parent):
        # TODO - this should probably live in a base deviceHandler.
        self.panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        # Readout mode control
        sizer.Add(wx.StaticText(self.panel, label="Readout mode"))
        modeButton = wx.Choice(self.panel, choices=self._modenames)
        self.updateModeButton(modeButton)
        sizer.Add(modeButton, flag=wx.EXPAND)
        events.subscribe(events.SETTINGS_CHANGED % self,
                         lambda: self.updateModeButton(modeButton))
        modeButton.Bind(wx.EVT_CHOICE, lambda evt: self.setReadoutMode(evt.GetSelection()))
        sizer.AddSpacer(4)
        # Gain control
        sizer.Add(wx.StaticText(self.panel, label="Gain"))
        gainButton = wx.Button(self.panel,
                               label="%s" % self.settings.get('gain', None))
        if 'gain' not in self.settings:
            gainButton.Disable()
        gainButton.Bind(wx.EVT_BUTTON, self.onGainButton)
        sizer.Add(gainButton, flag=wx.EXPAND)
        events.subscribe(events.SETTINGS_CHANGED % self,
                         lambda: gainButton.SetLabel("%s" % self.settings.get('gain', None)))
        sizer.AddSpacer(4)
        # Settings button
        adv_button = wx.Button(parent=self.panel, label='Settings')
        adv_button.Bind(wx.EVT_LEFT_UP, self.showSettings)
        sizer.Add(adv_button)
        self.panel.SetSizerAndFit(sizer)
        return self.panel


    def updateModeButton(self, button):
        button.Set(self._modenames)
        button.SetSelection(self.settings.get('readout mode', 0))


    def onGainButton(self, evt):
        if 'gain' not in self.settings:
            return
        desc = self.describe_setting('gain')
        mingain, maxgain = desc['values']
        gain = wx.GetNumberFromUser('Gain', '', 'Set gain', value=self.settings.get('gain', 0),
                                    min=mingain, max=maxgain)
        if gain == -1:
            return
        self.updateSettings({'gain': gain})

    @pauseVideo
    def setReadoutMode(self, index):
        if len(self.modes) <= 1:
            # Only one mode - nothing to do.
            return
        self.set_setting('readout mode', self.modes[index][0])
        self.updateSettings()

    def addPostProcessor(
        self,
        priority: int,
        f_data: collections.abc.Callable[np.ndarray, np.ndarray],
        f_shape: collections.abc.Callable[tuple[int, int], tuple[int, int]]
    ):
        """Add a post-processing unit.

        A post-processor must not change the pixel size of the data. This
        means no scaling and other similar transformations. Because of the
        way that data is saved, it is useful to know the expected shape in
        advance and hence it is required from post-processors to define a
        function to do so.

        Multiple post-processors with the same priority will be used in the
        order they were added.

        Args:
            priority: integer used to determine the order of the processing
            units.
            f_data: callable object used to process the image data; it should
            take as an input a 2D numpy array of image data and return a 2D
            numpy array with shape consistent with the f_shape argument.
            f_shape: callable object used to calculate the expected data
            shape of the output of the f_data argument; the format is (w, h).
        """
        self._post_processors.append(
            _PostProcessor(priority, f_data, f_shape)
        )
        self._post_processors.sort(key=lambda x: x.priority)

    def removePostProcessor(
        self,
        priority: int,
        f_data: typing.Optional[
            collections.abc.Callable[np.ndarray, np.ndarray]
        ] = None,
        f_shape: typing.Optional[
            collections.abc.Callable[tuple[int, int], tuple[int, int]]
        ] = None
    ):
        """Remove a post-processing unit.

        The data and shape functions can be used to narrow down the selected
        post-processor. The testing is done with a value comparison If
        multiple post-processors match the searching criteria, e.g. identical
        priority, then the selection is done by the order in which they were
        added.

        Args:
            priority: the priority of the post-processor
            f_data: the data processing function of the post-processor
            f_shape: the shape calculation function of the post-processor
        """
        # Filter by priority
        candidates = [
            item
            for item in self._post_processors
            if item.priority == priority
        ]
        if f_data:
            # Filter by data function
            candidates = [item for item in candidates if item.f_data == f_data]
        if f_shape:
            # Filter by shape function
            candidates = [
                item
                for item in candidates
                if item.f_shape == f_shape
            ]
        # Remove the first candidate
        self._post_processors.remove(candidates[0])
