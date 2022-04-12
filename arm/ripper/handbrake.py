#!/usr/bin/env python3
"""Handbrake processing of dvd/blu-ray"""

import sys
import os
import logging
import subprocess
import re
import shlex

from arm.ripper import utils
from arm.ui import app, db  # noqa E402
from arm.config.config import cfg

PROCESS_COMPLETE = "Handbrake processing complete"


def handbrake_main_feature(srcpath, basepath, logfile, job):
    """
    Process dvd with main_feature enabled.\n\n
    :param srcpath: Path to source for HB (dvd or files)\n
    :param basepath: Path where HB will save trancoded files\n
    :param logfile: Logfile for HB to redirect output to\n
    :param job: Disc object\n
    :return: None
    """
    hb_args = hb_preset = ""
    logging.info("Starting DVD Movie main_feature processing")
    logging.debug("Handbrake starting: ")
    logging.debug(f"\n\r{job.pretty_table()}")

    utils.database_updater({'status': "waiting_transcode"}, job)
    utils.sleep_check_process("HandBrakeCLI", int(cfg["MAX_CONCURRENT_TRANSCODES"]))
    logging.debug("Setting job status to 'transcoding'")
    utils.database_updater({'status': "transcoding"}, job)
    filename = os.path.join(basepath, job.title + "." + cfg["DEST_EXT"])
    filepathname = os.path.join(basepath, filename)
    logging.info(f"Ripping title main_feature to {shlex.quote(filepathname)}")

    get_track_info(srcpath, job)

    track = job.tracks.filter_by(main_feature=True).first()
    if track is None:
        msg = "No main feature found by Handbrake. Turn main_feature to false in arm.yml and try again."
        logging.error(msg)
        raise RuntimeError(msg)

    track.filename = track.orig_filename = filename
    db.session.commit()

    if job.disctype == "dvd":
        hb_args = cfg["HB_ARGS_DVD"]
        hb_preset = cfg["HB_PRESET_DVD"]
    elif job.disctype == "bluray":
        hb_args = cfg["HB_ARGS_BD"]
        hb_preset = cfg["HB_PRESET_BD"]

    cmd = f'nice {cfg["HANDBRAKE_CLI"]} -i {shlex.quote(srcpath)} -o {shlex.quote(filepathname)} ' \
          f'--main-feature --preset "{hb_preset}" {hb_args} >> {logfile} 2>&1'

    logging.debug(f"Sending command: {cmd}")

    try:
        subprocess.check_output(cmd, shell=True).decode("utf-8")
        logging.info("Handbrake call successful")
        track.status = "success"
    except subprocess.CalledProcessError as hb_error:
        err = f"Call to handbrake failed with code: {hb_error.returncode}({hb_error.output})"
        logging.error(err)
        track.status = "fail"
        track.error = err
        job.status = "fail"
        db.session.commit()
        sys.exit(err)

    logging.info(PROCESS_COMPLETE)
    logging.debug(f"\n\r{job.pretty_table()}")
    track.ripped = True
    db.session.commit()


def handbrake_all(srcpath, basepath, logfile, job):
    """
    Process all titles on the dvd\n
    :param srcpath: Path to source for HB (dvd or files)\n
    :param basepath: Path where HB will save trancoded files\n
    :param logfile: Logfile for HB to redirect output to\n
    :param job: Disc object\n
    :return: None
    """
    # Wait until there is a spot to transcode
    job.status = "waiting_transcode"
    db.session.commit()
    utils.sleep_check_process("HandBrakeCLI", int(cfg["MAX_CONCURRENT_TRANSCODES"]))
    job.status = "transcoding"
    db.session.commit()
    logging.info("Starting BluRay/DVD transcoding - All titles")

    hb_args, hb_preset = correct_hb_settings(job)

    get_track_info(srcpath, job)

    logging.debug(f"Total number of tracks is {job.no_of_titles}")

    for track in job.tracks:

        if track.length < int(cfg["MINLENGTH"]):
            # too short
            logging.info(f"Track #{track.track_number} of {job.no_of_titles}. "
                         f"Length ({track.length}) is less than minimum length ({cfg['MINLENGTH']}).  Skipping")
        elif track.length > int(cfg["MAXLENGTH"]):
            # too long
            logging.info(f"Track #{track.track_number} of {job.no_of_titles}. "
                         f"Length ({track.length}) is greater than maximum length ({cfg['MAXLENGTH']}).  Skipping")
        else:
            # just right
            logging.info(f"Processing track #{track.track_number} of {job.no_of_titles}. "
                         f"Length is {track.length} seconds.")

            filename = "title_" + str.zfill(str(track.track_number), 2) + "." + cfg["DEST_EXT"]
            filepathname = os.path.join(basepath, filename)

            logging.info(f"Transcoding title {track.track_number} to {shlex.quote(filepathname)}")

            track.filename = track.orig_filename = filename
            db.session.commit()

            cmd = f'nice {cfg["HANDBRAKE_CLI"]} -i {shlex.quote(srcpath)} -o {shlex.quote(filepathname)} ' \
                  f'--preset "{hb_preset}" -t {str(track.track_number)} {hb_args}>> {logfile} 2>&1'

            logging.debug(f"Sending command: {cmd}")

            try:
                hand_brake_output = subprocess.check_output(
                    cmd,
                    shell=True
                ).decode("utf-8")
                logging.debug(f"Handbrake exit code: {hand_brake_output}")
                track.status = "success"
            except subprocess.CalledProcessError as hb_error:
                err = f"Handbrake encoding of title {track.track_number} failed with code: {hb_error.returncode}" \
                      f"({hb_error.output})"
                logging.error(err)
                track.status = "fail"
                track.error = err

            track.ripped = True
            db.session.commit()

    logging.info(PROCESS_COMPLETE)
    logging.debug(f"\n\r{job.pretty_table()}")


def correct_hb_settings(job):
    """
    Get the correct custom arguments/presets for this disc
    :param job: The job
    :return: Correct preset and string arguments from A.R.M config
    """
    hb_args = ""
    hb_preset = ""
    if job.disctype == "dvd":
        hb_args = cfg["HB_ARGS_DVD"]
        hb_preset = cfg["HB_PRESET_DVD"]
    elif job.disctype == "bluray":
        hb_args = cfg["HB_ARGS_BD"]
        hb_preset = cfg["HB_PRESET_BD"]
    return hb_args, hb_preset


def handbrake_mkv(srcpath, basepath, logfile, job):
    """
    Process all mkv files in a directory.\n\n
    :param srcpath: Path to source for HB (dvd or files)\n
    :param basepath: Path where HB will save trancoded files\n
    :param logfile: Logfile for HB to redirect output to\n
    :param job: Disc object\n
    :return: None
    """
    # Added to limit number of transcodes
    job.status = "waiting_transcode"
    db.session.commit()
    utils.sleep_check_process("HandBrakeCLI", int(cfg["MAX_CONCURRENT_TRANSCODES"]))
    job.status = "transcoding"
    db.session.commit()
    hb_args, hb_preset = correct_hb_settings(job)

    # This will fail if the directory raw gets deleted
    for files in os.listdir(srcpath):
        srcpathname = os.path.join(srcpath, files)
        destfile = os.path.splitext(files)[0]
        filename = os.path.join(basepath, destfile + "." + cfg["DEST_EXT"])
        filepathname = os.path.join(basepath, filename)

        logging.info(f"Transcoding file {shlex.quote(files)} to {shlex.quote(filepathname)}")

        cmd = f'nice {cfg["HANDBRAKE_CLI"]} -i {shlex.quote(srcpathname)} -o {shlex.quote(filepathname)} ' \
              f'--preset "{hb_preset}" {hb_args}>> {logfile} 2>&1'

        logging.debug(f"Sending command: {cmd}")

        try:
            hand_break_output = subprocess.check_output(
                cmd,
                shell=True
            ).decode("utf-8")
            logging.debug(f"Handbrake exit code: {hand_break_output}")
        except subprocess.CalledProcessError as hb_error:
            err = f"Handbrake encoding of file {shlex.quote(files)} failed with code: {hb_error.returncode}" \
                  f"({hb_error.output})"
            logging.error(err)
            # job.errors.append(f)

    logging.info(PROCESS_COMPLETE)
    logging.debug(f"\n\r{job.pretty_table()}")


def get_track_info(srcpath, job):
    """
    Use HandBrake to get track info and updatte Track class\n\n
    :param srcpath: Path to disc\n
    :param job: Job instance\n
    :return: None
    """
    logging.info("Using HandBrake to get information on all the tracks on the disc.  This will take a few minutes...")

    cmd = f'{cfg["HANDBRAKE_CLI"]} -i {shlex.quote(srcpath)} -t 0 --scan'

    logging.debug(f"Sending command: {cmd}")
    hand_break_output = handbrake_char_encoding(cmd)

    t_pattern = re.compile(r'.*\+ title *')
    pattern = re.compile(r'.*duration:.*')
    seconds = 0
    t_no = 0
    fps = float(0)
    aspect = 0
    result = None
    main_feature = False
    for line in hand_break_output:

        # get number of titles
        if result is None:
            if job.disctype == "bluray":
                result = re.search('scan: BD has (.*) title(s)', line)
            else:
                result = re.search('scan: DVD has (.*) title(s)', line)  # noqa: W605

            if result:
                titles = result.group(1)
                titles = titles.strip()
                logging.debug(f"Line found is: {line}")
                logging.info(f"Found {titles} titles")
                job.no_of_titles = titles
                db.session.commit()

        main_feature, t_no = title_finder(aspect, fps, job, line, main_feature, seconds, t_no, t_pattern)
        seconds = seconds_builder(line, pattern, seconds)
        main_feature = is_main_feature(line, main_feature)

        if (re.search(" fps", line)) is not None:
            fps = line.rsplit(' ', 2)[-2]
            aspect = line.rsplit(' ', 3)[-3]
            aspect = str(aspect).replace(",", "")

    utils.put_track(job, t_no, seconds, aspect, fps, main_feature, "HandBrake")


def title_finder(aspect, fps, job, line, main_feature, seconds, t_no, t_pattern):
    """

    :param aspect:
    :param fps:
    :param job:
    :param line:
    :param main_feature:
    :param seconds:
    :param t_no:
    :param t_pattern:
    :return: None
    """
    if (re.search(t_pattern, line)) is not None:
        if t_no != 0:
            utils.put_track(job, t_no, seconds, aspect, fps, main_feature, "HandBrake")

        main_feature = False
        t_no = line.rsplit(' ', 1)[-1]
        t_no = t_no.replace(":", "")
    return main_feature, t_no


def is_main_feature(line, main_feature):
    """
    Check if we can find 'Main Feature' in hb output line\n
    :param str line: Line from HandBrake output
    :param bool main_feature:
    :return bool main_feature: Return true if we fine main feature
    """
    if (re.search("Main Feature", line)) is not None:
        main_feature = True
    return main_feature


def seconds_builder(line, pattern, seconds):
    """
    Find the track time and convert to seconds\n
    :param line: Line from HandBrake output
    :param pattern: regex patter
    :param seconds:
    :return:
    """
    if (re.search(pattern, line)) is not None:
        time = line.split()
        hour, mins, secs = time[2].split(':')
        seconds = int(hour) * 3600 + int(mins) * 60 + int(secs)
    return seconds


def handbrake_char_encoding(cmd):
    """
    Allows us to try multiple char encoding types\n\n
    :param cmd: CMD to push
    :return: the output from HandBrake or -1 if it fails
    """
    charset_found = False
    hand_brake_output = -1
    try:
        hand_brake_output = subprocess.check_output(
            cmd,
            stderr=subprocess.STDOUT,
            shell=True
        ).decode('utf-8', 'ignore').splitlines()
    except subprocess.CalledProcessError as hb_error:
        logging.error("Couldn't find a valid track with utf-8 encoding. Trying with cp437")
        logging.error(f"Specific error is: {hb_error}")
    else:
        charset_found = True
    if not charset_found:
        try:
            hand_brake_output = subprocess.check_output(
                cmd,
                stderr=subprocess.STDOUT,
                shell=True
            ).decode('cp437').splitlines()
        except subprocess.CalledProcessError as hb_error:
            logging.error("Couldn't find a valid track. "
                          "Try running the command manually to see more specific errors.")
            logging.error(f"Specific error is: {hb_error}")
            # If it doesn't work now we either have bad encoding or HB has ran into issues
    return hand_brake_output
