"""Database Model for Drive Information on the System
"""

import fcntl
import logging
import os
import re
import subprocess

from arm.ui import db


# ioctl defines may (not very likely) change.
# see https://github.com/torvalds/linux/blob/master/include/uapi/linux/cdrom.h
CDS_NO_INFO = 0
CDS_NO_DISC = 1
CDS_TRAY_OPEN = 2
CDS_DRIVE_NOT_READY = 3
CDS_DISC_OK = 4


def _tray_status(devpath, logger=logging):
    """
    Get the Status of the CDROM Drive.

    Note: This should be considered as internal function to the
          SystemDrives class and should not be called directly.

    Parameters
    ----------
    devpath: str
        path to cdrom

    Returns
    -------
    int or None
        Returns `None` if a (known) error occured and `int` on
        success.  The values are defined in the linux kernel and
        referenced here:

        - `CDS_NO_INFO`
        - `CDS_NO_DISC`
        - `CDS_TRAY_OPEN`
        - `CDS_DRIVE_NOT_READY`
        - `CDS_DISC_OK`

        ioctl defines may (not very likely) change. See
        [linux/cdrom.h](https://github.com/torvalds/linux/blob/master/include/uapi/linux/cdrom.h)
        for specifics.
    """
    try:
        disk_check = os.open(devpath, os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError as err:
        logger.critical(f"Possibly Stale Mount Points detected: '{err}'")
        return None  # should be cleared in `ui.settings.DriveUtils.drives_update`
    except TypeError as err:
        logger.critical(f"Possible Database Inconsistency for {devpath}: '{err:s}'")
        return None  # inconsistency in SystemDrives.mount
    except OSError as err:
        # Sometimes ARM will log errors opening hard drives. this check should stop it
        if not re.search(r'hd[a-j]|sd[a-j]|loop\d|nvme\d', devpath):
            logger.critical(f"The device '{devpath}' is not an optical drive")
            return None  # inconsistency in SystemDrives.mount
        raise err
    try:
        return fcntl.ioctl(disk_check, 0x5326, 0)
    except OSError as err:
        logger.warning(f"Failed to check status for '{devpath}': {err:s}")
        return None
    finally:
        os.close(disk_check)


class SystemDrives(db.Model):  # pylint: disable=too-many-instance-attributes
    """
    Class to hold the system cd/dvd/Blu-ray drive information
    """
    drive_id = db.Column(db.Integer, index=True, primary_key=True)

    # static information:
    name = db.Column(db.String(100))  # maker+serial (static identification)
    maker = db.Column(db.String(25))
    model = db.Column(db.String(50))
    serial = db.Column(db.String(25))
    connection = db.Column(db.String(5))
    read_cd = db.Column(db.Boolean)
    read_dvd = db.Column(db.Boolean)
    read_bd = db.Column(db.Boolean)

    # dynamic information (subject to change):
    mount = db.Column(db.String(100))  # mount point (may change on startup)
    firmware = db.Column(db.String(10))
    location = db.Column(db.String(255))
    stale = db.Column(db.Boolean)  # indicate that this drive was not found.
    mdisc = db.Column(db.SmallInteger)

    # cross references:
    job_id_current = db.Column(db.Integer, db.ForeignKey("job.job_id"))
    job_id_previous = db.Column(db.Integer, db.ForeignKey("job.job_id"))
    drive_mode = db.Column(db.String(100))
    # relationship - join current and previous jobs to the jobs table
    job_current = db.relationship("Job", backref="Current", foreign_keys=[job_id_current])
    job_previous = db.relationship("Job", backref="Previous", foreign_keys=[job_id_previous])

    # user input:
    description = db.Column(db.Unicode(200))  # user input

    def __init__(self):
        # mark drive info as outdated
        self.stale = True
        self.mdisc = None

        # cross references
        self.job_id_current = None
        self.job_id_previous = None

        # user input
        self.description = ""
        self.drive_mode = "auto"

    def update(self, drive):
        """
        Update database object with drive information

        Parameters
        ----------
        drive: arm.ui.settings.DriveUtils.Drive
        """
        # static information
        self.name = drive.name
        self.maker = drive.maker
        self.model = drive.model
        self.serial = drive.serial
        self.connection = drive.connection
        self.read_cd = drive.read_cd
        self.read_dvd = drive.read_dvd
        self.read_bd = drive.read_bd
        # dynamic information
        self.mount = drive.mount
        self.firmware = drive.firmware
        self.location = drive.location
        # mark drive info as updated
        self.stale = False
        # remove MakeMKV disc id
        self.mdisc = None

    @property
    def type(self):
        """find the Drive type (CD, DVD, Blu-ray) from the udev values"""
        temp = ""
        if self.read_cd:
            temp += "CD"
        if self.read_dvd:
            temp += "/DVD"
        if self.read_bd:
            temp += "/BluRay"
        return temp

    @property
    def tray(self):
        """Numeric tray status.

        See `_tray_status`
        """
        status = _tray_status(self.mount)
        logging.debug(f"Drive '{self.mount}': tray status '{status}'")
        return status

    @property
    def open(self):
        """Drive tray open"""
        return self.tray == CDS_TRAY_OPEN

    @property
    def ready(self):
        """Drive has medium loaded and is ready for reading."""
        return self.tray == CDS_DISC_OK

    def open_close(self, logger=logging):
        """Open or Close the drive

        Note: --traytoggle is not ejecting for some drives and cannot check for
              running jobs
        """
        cmd = []
        if self.open:
            cmd = ["--trayclose"]
        elif self.job_id_current:
            logger.debug(f"{self.mount} unable to eject. Job [{self.job_id_current}] in progress.")
            return
        cmd = ["eject"] + cmd + ["--verbose", self.mount]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as err:
            logger.debug(err.stdout + err.stderr)
            logger.info(f"Failed to eject {self.mount}.")
            return err.stderr
        else:
            logger.info(f"Ejected {self.mount}")
        return None

    def debug(self, logger=logging):
        """
        Report the current drive status (debug)
        """
        logger.debug("*********")
        logger.debug(f"Name: '{self.name}'")
        logger.debug(f"Type: {self.type}")
        logger.debug(f"Description: '{self.description}'")
        logger.debug(f"Mount: '{self.mount}'", self.mount)
        jobs = (
            ("Current", self.job_id_current, self.job_current),
            ("Previous", self.job_id_previous, self.job_previous),
        )
        for job_name, job_id, job in jobs:
            logger.debug(f"Job {job_name}: {self.job_id_current})")
            if job_id and job is not None:
                logger.debug(f"Job - Status: {job.status}")
                logger.debug(f"Job - Type: {job.video_type}")
                logger.debug(f"Job - Title: {job.title}")
                logger.debug(f"Job - Year: {job.year}")
        logger.debug("*********")

    def new_job(self, job_id):
        """new job assigned to the drive, update with new job id, and previous job_id"""
        self.job_id_previous = self.job_id_current
        self.job_id_current = job_id

    def job_finished(self):
        """update Job IDs between current and previous jobs"""
        self.job_id_previous = self.job_id_current
        self.job_id_current = None
