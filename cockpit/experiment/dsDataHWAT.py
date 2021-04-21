#!/usr/bin/env python
# -*- coding: utf-8 -*-

from cockpit import depot
from cockpit import events
from cockpit.experiment.experiment import Experiment
from cockpit.experiment import actionTable
import cockpit.util.threads
import cockpit.interfaces.imager as imager
import cockpit.interfaces.stageMover
import decimal
# import cockpit.util.user
import wx
import os
import time
import numpy as np
import imageio

import cockpit.util.userConfig as Config


## Provided so the UI knows what to call this experiment.
EXPERIMENT_NAME = "ATBiasImageDatasetExperiment"
CAMERA_TIMEOUT = 2.0


class ATBiasImageDatasetExperiment(Experiment):
    """Create BiasImageDatasetExperiment from parent class (the ImmediateModeExperiment)
    Potentially we should directly create an Experiment, for ActionTable efficiency reasons,
    but this should be a good starting point
    We are using a composite AO device for applying a sequence of aberrations"""

    def __init__(
        self,
        *args,
        bias_modes=(4, 5, 6, 7, 10),
        abb_magnitude=5,
        applied_modes=(4, 5, 6, 7, 10),
        applied_step=50,
        numReps=1,
        initial_abb=None,
        repDuration=4,
        imagesPerRep=1,
        saveprefix="BIDE_",
        altBottom=0,
        zHeight=0,
        sliceHeight=0 ,
        exposureSettings=[],
        **kwargs,
    ):
        areas = numReps
        self.bias_modes = bias_modes  # 0-indexed-Noll
        print(bias_modes)
        applied_steps = np.linspace(-abb_magnitude, abb_magnitude, applied_step)

        self.abb_generator = self.generateAbb(
            bias_modes, applied_modes, applied_steps, areas
        )  # Not going to be thread safe
        try:
            self.saveBasePath = os.path.join(cockpit.util.userConfig.getValue("data-dir"), saveprefix)
        except:
            self.saveBasePath = os.path.join(os.path.expanduser("~"), saveprefix)

        self.numReps = len(applied_steps)* len(applied_modes) * areas

        # Set savepath to '' to prevent saving images. We will save our own images,
        # because setting filenames using Experiment/DataSaver look like it is
        # going to require a bunch of complicated subclassing
        self.zPositioner=None
		
        self.aodev = cockpit.depot.getDeviceWithName("ao")
        self.dmHandler = cockpit.depot.getDeviceWithName("dm").handler
		
        super().__init__(numReps=self.numReps, repDuration=repDuration,altBottom=altBottom,
        zHeight=zHeight,sliceHeight=sliceHeight,zPositioner=self.zPositioner,exposureSettings=exposureSettings,otherHandlers=[cockpit.depot.getDeviceWithName("dm").handler],**kwargs)

        # override cameraToImageCount so bias images are correctly saved to individual files
        # (necessary if using DataSaver)
        self.cameraToImageCount = 2 * len(bias_modes) + 1


        
        self.table = None  # Apparently we need this, even though we're not using it? suspect there is a problem with ImmediateModeExperiment
        self.time_start = time.time()
        
    ## Create the ActionTable needed to run the experiment. We do three
    # Z-stacks for three different angles, and take five images at each
    # Z-slice, one for each phase.
    def generateActions(self):
        table = actionTable.ActionTable()
        curTime=0
		
        delayBeforeImaging=decimal.Decimal('.001')
        acc_patterns = []
        for biaslist, fprefix, newarea in self.abb_generator:
        
            acc_patterns.extend(biaslist)
            
            for abb in biaslist:
            
                self.dmHandler.addToggle(curTime,table)
                curTime += decimal.Decimal(self.dmHandler.getMovementTime())
                curTime += decimal.Decimal('.001')

                curTime += delayBeforeImaging
                
                # Image the sample.
                for cameras, lightTimePairs in self.exposureSettings:
                    curTime = self.expose(curTime, cameras, lightTimePairs, table)
        # TypeError: '>' not supported between instances of 'list' and 'float'
        self.aodev.proxy.queue_patterns(acc_patterns)
        
    def makeBiasPolytope(self, start_aberrations, offset_axes, nk, steps=(1,)):
        """Return list of list of zernike amplitudes ('betas') for generating cross-polytope pattern of psfs
        """
        # beta (diffraction-limited), N_beta = cpsf.czern.nk
        beta = np.zeros(nk, dtype=np.float32)
        beta[:] = start_aberrations

        # add offsets to beta

        betas = []
        betas.append(tuple(beta))
        for axis in offset_axes:
            for step in steps:
                plus_offset = beta.copy()
                plus_offset[axis] += 1 * step
                betas.append(tuple(plus_offset))
            for step in steps:
                minus_offset = beta.copy()
                minus_offset[axis] -= 1 * step
                betas.append(tuple(minus_offset))

        return betas

    def generateAbb(self, bias_modes, applied_modes, applied_steps, areas):
        """Returns each list of bias aberrations for AO device to apply"""
        start_aberrations = np.zeros(np.max((np.max(bias_modes), (np.max(applied_modes)))) + 1)
        newarea = False
        for area in range(areas):
            if area:
                newarea = True

            for applied_abb in applied_modes:
                for step in applied_steps:
                    start_aberrations[applied_abb] = step
                    biaslist = self.makeBiasPolytope(start_aberrations, bias_modes, len(start_aberrations))
                    fprefix = f"R{areas}A{area}A{applied_abb}S{step:.1f}_"
                    yield biaslist, fprefix, newarea
                    newarea = False


EXPERIMENT_CLASS = ATBiasImageDatasetExperiment  # Don't know what the point of this is but is required by GUI
