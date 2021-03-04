.. Copyright (C) 2021 David Miguel Susano Pinto <david.pinto@bioch.ox.ac.uk>

   Permission is granted to copy, distribute and/or modify this
   document under the terms of the GNU Free Documentation License,
   Version 1.3 or any later version published by the Free Software
   Foundation; with no Invariant Sections, no Front-Cover Texts, and
   no Back-Cover Texts.  A copy of the license is included in the
   section entitled "GNU Free Documentation License".

   Copyright (C) 2013, The Regents of University of California

   Redistribution and use in source and binary forms, with or without
   modification, are permitted provided that the following conditions
   are met:

   1. Redistributions of source code must retain the above copyright
     notice, this list of conditions and the following disclaimer.

   2. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in
     the documentation and/or other materials provided with the
     distribution.

   3. Neither the name of the copyright holder nor the names of its
     contributors may be used to endorse or promote products derived
     from this software without specific prior written permission.

   THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
   "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
   LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
   FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
   COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
   INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
   BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
   LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
   CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
   LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
   ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
   POSSIBILITY OF SUCH DAMAGE.

Architecture
############

Cockpit is implemented in the Python programming language.  The code
consists of four main components: devices, handlers, interfaces, and
the GUI.  Devices represent the individual physical devices, handlers
represent control components of the devices, handlers from single or
multiple devices are aggregated into interfaces, and the GUI component
provides the user interaction to the different interfaces and
handlers.

The Cockpit program starts in :func:`cockpit.main` which reads the
configuration files and constructs an :class:`cockpit.CockpitApp`
object.  This ``CockpitApp`` instance constructs a
:class:`cockpit.depot.DeviceDepot` and initialises all the devices
declared on the depot configuration.  Each device constructs their
associated handlers are initialised which are then collected by the
device depot.  Once devices and handlers are initialised, interfaces
are initialised.  Finally, the multiple GUI windows are created based
on the different handlers and interfaces available.  Once
initialisation is completed, we simply enter the event loop (provided
by the wxPython library) and wait for the user to do something.

.. todo::

    Consider using sphin-apidoc to generate the rest of this page.

cockpit.devices package
=======================

.. automodule:: cockpit.devices

cockpit.handlers package
========================

.. automodule:: cockpit.handlers

cockpit.events module
=====================

.. automodule:: cockpit.events

cockpit.interfaces package
==========================

.. automodule:: cockpit.interfaces

cockpit.gui package
===================

.. automodule:: cockpit.gui

cockpit.experiment package
==========================

.. automodule:: cockpit.experiment

cockpit.util package
====================

.. automodule:: cockpit.util
