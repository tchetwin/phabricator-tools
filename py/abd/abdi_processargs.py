"""Process the arguments for a single repository and execute."""
# =============================================================================
# CONTENTS
# -----------------------------------------------------------------------------
# abdi_processargs
#
# Public Functions:
#   setup_parser
#   setup_repo_arg_parser
#   configure_sendmail
#   setup_sigterm_handler
#   make_exception_message_handler
#   make_exception_delay_handler
#   get_retry_delays
#   run_once
#
# -----------------------------------------------------------------------------
# (this contents block is generated, edits will be lost)
# =============================================================================

from __future__ import absolute_import

import contextlib
import platform
import signal
import sys
import traceback

import phlcon_reviewstatecache
import phlmail_sender
import phlsys_conduit
import phlsys_git
import phlsys_pluginmanager
import phlsys_sendmail
import phlsys_strtotime

import abdmail_mailer
import abdt_classicnaming
import abdt_compositenaming
import abdt_conduit
import abdt_errident
import abdt_git
import abdt_rbranchnaming
import abdt_repooptions
import abdt_reporeporter
import abdt_shareddictoutput
import abdt_tryloop

import abdi_processrepo


def setup_parser(parser):
    parser.add_argument(
        '--sys-admin-emails',
        metavar="EMAIL",
        nargs="+",
        type=str,
        required=True,
        help="email addresses to send important system events to")
    parser.add_argument(
        '--sendmail-binary',
        metavar="PROGRAM",
        type=str,
        default="sendmail",
        help="program to send the mail with (e.g. sendmail, catchmail)")
    parser.add_argument(
        '--sendmail-type',
        metavar="TYPE",
        type=str,
        default="sendmail",
        help="type of program to send the mail with (sendmail, catchmail), "
        "this will affect the parameters that Arycd will use.")


def setup_repo_arg_parser(parser):

    parser.add_argument(
        '--instance-uri',
        type=str,
        metavar='ADDRESS',
        required=True,
        help="URI to use to access the conduit API, e.g. "
             "'http://127.0.0.1/api/'.")

    parser.add_argument(
        '--arcyd-user',
        type=str,
        metavar='USERNAME',
        required=True,
        help="username of admin account registered for arcyd to use.")

    parser.add_argument(
        '--arcyd-cert',
        metavar="CERT",
        type=str,
        required=True,
        help="Phabricator Conduit API certificate to use, this is the "
        "value that you will find in your user account in Phabricator "
        "at: http://your.server.example/settings/panel/conduit/. "
        "It can also be found in ~/.arcrc.")

    parser.add_argument(
        '--arcyd-email',
        metavar="FROM",
        type=str,
        required=True,
        help="email address for Arcyd to send mails from")

    parser.add_argument(
        '--admin-email',
        metavar="TO",
        type=str,
        required=True,
        help="single email address to send important system events to")

    parser.add_argument(
        '--repo-desc',
        metavar="DESC",
        type=str,
        required=True,
        help="description to use in emails")

    parser.add_argument(
        '--repo-path',
        metavar="PATH",
        type=str,
        required=True,
        help="path to the repository on disk")

    parser.add_argument(
        '--repo-snoop-url',
        metavar="URL",
        type=str,
        help="URL to use to snoop the latest contents of the repository, this "
             "is used by Arcyd to more efficiently determine if it needs to "
             "fetch the repository or not.  The efficiency comes from "
             "re-using connections to the same host when querying.  The "
             "contents returned by the URL are expected to change every time "
             "the git repository changes, a good example of a URL to supply "
             "is to the 'info/refs' address if you're serving up the repo "
             "over http or https.  "
             "e.g. 'http://server.test/git/myrepo/info/refs'.")

    parser.add_argument(
        '--https-proxy',
        metavar="PROXY",
        type=str,
        help="proxy to use, if necessary")

    parser.add_argument(
        '--sleep-secs',
        metavar="TIME",
        type=int,
        default=60,
        help="time to wait between fetches")

    parser.add_argument(
        '--review-url-format',
        type=str,
        metavar='STRING',
        required=True,
        help="a format string for generating URLs for viewing reviews, e.g. "
             "something like this: "
             "'http://my.phabricator/D{review}' , "
             "note that the {review} will be substituted for the id of the "
             "branch.")

    parser.add_argument(
        '--branch-url-format',
        type=str,
        metavar='STRING',
        required=True,
        help="a format string for generating URLs for viewing branches, e.g. "
             "for a gitweb install: "
             "'http://my.git/gitweb?p=r.git;a=log;h=refs/heads/{branch}', "
             "note that the {branch} will be substituted for the branch name. "
             "will be used on the dashboard to link to branches.")

    parser.add_argument(
        '--try-touch-path',
        metavar="PATH",
        type=str,
        required=True,
        help="file to touch when trying to update a repo")

    parser.add_argument(
        '--ok-touch-path',
        metavar="PATH",
        type=str,
        required=True,
        help="file to touch when successfully updated a repo")

    parser.add_argument(
        "--plugins",
        metavar="MODULE_NAME",
        nargs="+",
        type=str,
        default=[],
        required=False,
        help="List the plugins to be loaded. MODULE_NAME must be present "
        "in /testbed/plugins/ directory as this feature is WIP.")

    parser.add_argument(
        "--trusted-plugins",
        metavar="MODULE_NAME",
        nargs="+",
        type=str,
        default=[],
        required=False,
        help="List the trusted plugins to be loaded. MODULE_NAME must be "
        "present in /testbed/plugins/ directory as this feature is WIP."
        "See /testbed/plugins/README.md for detail about trusted-plugins")


def configure_sendmail(args):
    if args.sendmail_binary:
        phlsys_sendmail.Sendmail.set_default_binary(
            args.sendmail_binary)

    if args.sendmail_type:
        phlsys_sendmail.Sendmail.set_default_params_from_type(
            args.sendmail_type)


# XXX: belongs in phlsys somewhere
def setup_sigterm_handler():
    def HandleSigterm(unused1, unused2):
        # raises 'SystemExit' exception, which will allow us to clean up
        sys.exit(1)
    signal.signal(signal.SIGTERM, HandleSigterm)


def _send_mail(mailsender, emails, uname, subject, tb, body_prefix, message):
    body = uname + "\n" + tb
    body += str(body_prefix)
    body += str(message)
    print body
    mailsender.send(
        subject=str(subject),
        message=body,
        to_addresses=emails)


def make_exception_message_handler(
        args, arcyd_reporter, repo, subject, body_prefix):
    uname = str(platform.uname())
    emails = args.sys_admin_emails

    mailsender = phlmail_sender.MailSender(
        phlsys_sendmail.Sendmail(),
        "arcyd@" + platform.node())

    def msg_exception(message):
        tb = traceback.format_exc()
        _send_mail(
            mailsender, emails, uname, subject, tb, body_prefix, message)

        short_tb = traceback.format_exc(1)
        detail = ""
        if repo:
            detail += "processing repo: {repo}\n".format(repo=repo)
        detail += "exception: {exception}".format(exception=short_tb)
        arcyd_reporter.log_system_error('processargs', detail)

    return msg_exception


def make_exception_delay_handler(args, arcyd_reporter, repo):
    return make_exception_message_handler(
        args,
        arcyd_reporter,
        repo,
        "arcyd paused with exception",
        "will wait: ")


def get_retry_delays():
    strToTime = phlsys_strtotime.duration_string_to_time_delta
    retry_delays = [strToTime(d) for d in ["10 minutes", "1 hours"]]
    return retry_delays


def run_once(repo, args, out, arcyd_reporter, conduits, url_watcher):

    reporter = abdt_reporeporter.RepoReporter(
        arcyd_reporter,
        repo,
        args.repo_desc,
        abdt_shareddictoutput.ToFile(args.try_touch_path),
        abdt_shareddictoutput.ToFile(args.ok_touch_path))

    with arcyd_reporter.tag_timer_context('process args'):
        with contextlib.closing(reporter):
            _run_once(
                args, out, reporter, arcyd_reporter, conduits, url_watcher)


def _set_attrib_if_not_none(config, key, value):
    if value:
        getattr(config, key)  # raise if 'key' doesn't exist already
        setattr(config, key, value)


def _make_config_from_args(args):
    config = abdt_repooptions.Data()
    if args.admin_email:
        config.admin_emails = [args.admin_email]
    _set_attrib_if_not_none(
        config, 'description', args.repo_desc)
    _set_attrib_if_not_none(
        config, 'branch_url_format', args.branch_url_format)
    _set_attrib_if_not_none(
        config, 'review_url_format', args.review_url_format)
    return config


def _determine_options(args, repo):
    # combine all the available configs
    default_config = abdt_repooptions.make_default_data()
    args_config = _make_config_from_args(args)
    repo_config = abdt_repooptions.data_from_repo_or_none(repo)
    config = abdt_repooptions.merge_data_objects(
        default_config, args_config, repo_config)
    abdt_repooptions.validate_data(config)
    return config


def _run_once(args, out, reporter, arcyd_reporter, conduits, url_watcher):

    with arcyd_reporter.tag_timer_context('process branches prolog'):
        repo = abdt_git.Repo(
            phlsys_git.Repo(args.repo_path), "origin", args.repo_desc)

        arcyd_reporter.tag_timer_decorate_object_methods_individually(
            repo, 'git')

        _fetch_if_needed(
            url_watcher,
            args.repo_snoop_url,
            repo,
            args.repo_desc)

        options = _determine_options(args, repo)

        arcyd_conduit = _connect(conduits, args, arcyd_reporter)

        reporter.set_config(options)

        sender = phlmail_sender.MailSender(
            phlsys_sendmail.Sendmail(), args.arcyd_email)

        # TODO: this should be a URI for users not conduit
        mailer = abdmail_mailer.Mailer(
            sender,
            options.admin_emails,
            options.description,
            args.instance_uri)

        pluginManager = phlsys_pluginmanager.PluginManager(
            args.plugins, args.trusted_plugins)

        out.display("process (" + options.description + "): ")

        branch_url_callable = None
        if options.branch_url_format:
            def make_branch_url(branch_name):
                return options.branch_url_format.format(branch=branch_name)
            branch_url_callable = make_branch_url

        branch_naming = abdt_compositenaming.Naming(
            abdt_classicnaming.Naming(),
            abdt_rbranchnaming.Naming())

        branches = abdt_git.get_managed_branches(
            repo,
            options.description,
            branch_naming,
            branch_url_callable)

        for branch in branches:
            arcyd_reporter.tag_timer_decorate_object_methods_individually(
                branch, 'branch')

    try:
        with arcyd_reporter.tag_timer_context('process branches'):
            abdi_processrepo.process_branches(
                branches,
                arcyd_conduit,
                mailer,
                pluginManager,
                reporter)
    except Exception:
        reporter.on_traceback(traceback.format_exc())
        raise

    reporter.on_completed()


def _fetch_if_needed(url_watcher, snoop_url, repo, repo_desc):

    did_fetch = False

    # fetch only if we need to
    if not snoop_url or url_watcher.peek_has_url_recently_changed(snoop_url):
            abdt_tryloop.tryloop(
                repo.fetch_prune, abdt_errident.FETCH_PRUNE, repo_desc)
            did_fetch = True

    if did_fetch and snoop_url:
        # consume the 'newness' of this repo, since fetching succeeded
        url_watcher.has_url_recently_changed(snoop_url)

    return did_fetch


def _connect(conduits, args, arcyd_reporter):

    key = (
        args.instance_uri, args.arcyd_user, args.arcyd_cert, args.https_proxy)
    if key not in conduits:
        # create an array so that the 'connect' closure binds to the 'conduit'
        # variable as we'd expect, otherwise it'll just modify a local variable
        # and this 'conduit' will remain 'None'
        # XXX: we can do better in python 3.x (nonlocal?)
        conduit = [None]

        def connect():
            # nonlocal conduit # XXX: we'll rebind in python 3.x, instead
            conduit[0] = phlsys_conduit.Conduit(
                args.instance_uri,
                args.arcyd_user,
                args.arcyd_cert,
                https_proxy=args.https_proxy)

        with arcyd_reporter.tag_timer_context('conduit connect'):
            abdt_tryloop.tryloop(
                connect, abdt_errident.CONDUIT_CONNECT, args.instance_uri)

        conduit = conduit[0]
        arcyd_reporter.tag_timer_decorate_object_methods_individually(
            conduit, 'base_conduit')
        reviewstate_cache = phlcon_reviewstatecache.ReviewStateCache()
        reviewstate_cache.set_conduit(conduit)
        arcyd_conduit = abdt_conduit.Conduit(conduit, reviewstate_cache)
        arcyd_reporter.tag_timer_decorate_object_methods_individually(
            arcyd_conduit, 'conduit')
        conduits[key] = arcyd_conduit
    else:
        arcyd_conduit = conduits[key]

    return arcyd_conduit


#------------------------------------------------------------------------------
# Copyright (C) 2013-2014 Bloomberg Finance L.P.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.
#------------------------------- END-OF-FILE ----------------------------------
