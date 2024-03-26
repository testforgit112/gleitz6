#!/usr/bin/env python

######################################################
#
# howdoi - instant coding answers via the command line
# written by Benjamin Gleitzman (gleitz@mit.edu)
# inspired by Rich Jones (rich@anomos.info)
#
######################################################

from __future__ import print_function
import gc
gc.disable()  # noqa: E402
import argparse
import os
import appdirs
import re
from cachelib import FileSystemCache, NullCache
import json
import requests
import sys
from . import __version__

from keep import utils as keep_utils

from pygments import highlight
from pygments.lexers import guess_lexer, get_lexer_by_name
from pygments.formatters.terminal import TerminalFormatter
from pygments.util import ClassNotFound

from pyquery import PyQuery as pq
from requests.exceptions import ConnectionError
from requests.exceptions import SSLError


# Handle imports for Python 2 and 3
if sys.version < '3':
    import codecs
    from urllib import quote as url_quote
    from urllib import getproxies
    from urlparse import urlparse, parse_qs

    # Handling Unicode: http://stackoverflow.com/a/6633040/305414
    def u(x):
        return codecs.unicode_escape_decode(x)[0]
else:
    from urllib.request import getproxies
    from urllib.parse import quote as url_quote, urlparse, parse_qs

    def u(x):
        return x


# rudimentary standardized 3-level log output
def _print_err(x): print("[ERROR] " + x)


_print_ok = print  # noqa: E305
def _print_dbg(x): print("[DEBUG] " + x)  # noqa: E302


if os.getenv('HOWDOI_DISABLE_SSL'):  # Set http instead of https
    SCHEME = 'http://'
    VERIFY_SSL_CERTIFICATE = False
else:
    SCHEME = 'https://'
    VERIFY_SSL_CERTIFICATE = True


SUPPORTED_SEARCH_ENGINES = ('google', 'bing', 'duckduckgo')

URL = os.getenv('HOWDOI_URL') or 'stackoverflow.com'

USER_AGENTS = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10.7; rv:11.0) Gecko/20100101 Firefox/11.0',
               'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:22.0) Gecko/20100 101 Firefox/22.0',
               'Mozilla/5.0 (Windows NT 6.1; rv:11.0) Gecko/20100101 Firefox/11.0',
               ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_7_4) AppleWebKit/536.5 (KHTML, like Gecko) '
                'Chrome/19.0.1084.46 Safari/536.5'),
               ('Mozilla/5.0 (Windows; Windows NT 6.1) AppleWebKit/536.5 (KHTML, like Gecko) Chrome/19.0.1084.46'
                'Safari/536.5'), )
SEARCH_URLS = {
    'bing': SCHEME + 'www.bing.com/search?q=site:{0}%20{1}&hl=en',
    'google': SCHEME + 'www.google.com/search?q=site:{0}%20{1}&hl=en',
    'duckduckgo': SCHEME + 'duckduckgo.com/?q=site:{0}%20{1}&t=hj&ia=web'
}

BLOCK_INDICATORS = (
    'form id="captcha-form"',
    'This page appears when Google automatically detects requests coming from your computer '
    'network which appear to be in violation of the <a href="//www.google.com/policies/terms/">Terms of Service'
)

BLOCKED_QUESTION_FRAGMENTS = (
    'webcache.googleusercontent.com',
)

STAR_HEADER = u('\u2605')
ANSWER_HEADER = u('{2}  Answer from {0} {2}\n{1}')
NO_ANSWER_MSG = '< no answer given >'

CACHE_EMPTY_VAL = "NULL"
CACHE_DIR = appdirs.user_cache_dir('howdoi')
CACHE_ENTRY_MAX = 128

HTML_CACHE_PATH = 'cache_html'
SUPPORTED_HELP_QUERIES = ['use howdoi', 'howdoi', 'run howdoi',
                          'do howdoi', 'howdoi howdoi', 'howdoi use howdoi']

if os.getenv('HOWDOI_DISABLE_CACHE'):
    cache = NullCache()  # works like an always empty cache
else:
    cache = FileSystemCache(CACHE_DIR, CACHE_ENTRY_MAX, default_timeout=0)

howdoi_session = requests.session()


class BlockError(RuntimeError):
    pass


def _random_int(width):
    bres = os.urandom(width)
    if sys.version < '3':
        ires = int(bres.encode('hex'), 16)
    else:
        ires = int.from_bytes(bres, 'little')

    return ires


def _random_choice(seq):
    return seq[_random_int(1) % len(seq)]


def get_proxies():
    proxies = getproxies()
    filtered_proxies = {}
    for key, value in proxies.items():
        if key.startswith('http'):
            if not value.startswith('http'):
                filtered_proxies[key] = 'http://%s' % value
            else:
                filtered_proxies[key] = value
    return filtered_proxies


def _format_url_to_filename(url, file_ext='html'):
    filename = ''.join(ch for ch in url if ch.isalnum())
    return filename + '.' + file_ext


def _get_result(url):
    try:
        return howdoi_session.get(url, headers={'User-Agent': _random_choice(USER_AGENTS)},
                                  proxies=get_proxies(),
                                  verify=VERIFY_SSL_CERTIFICATE).text
    except requests.exceptions.SSLError as e:
        _print_err('Encountered an SSL Error. Try using HTTP instead of '
                   'HTTPS by setting the environment variable "HOWDOI_DISABLE_SSL".\n')
        raise e


def _add_links_to_text(element):
    hyperlinks = element.find('a')

    for hyperlink in hyperlinks:
        pquery_object = pq(hyperlink)
        href = hyperlink.attrib['href']
        copy = pquery_object.text()
        if (copy == href):
            replacement = copy
        else:
            replacement = "[{0}]({1})".format(copy, href)
        pquery_object.replace_with(replacement)


def get_text(element):
    ''' return inner text in pyquery element '''
    _add_links_to_text(element)
    try:
        return element.text(squash_space=False)
    except TypeError:
        return element.text()


def _extract_links_from_bing(html):
    html.remove_namespaces()
    return [a.attrib['href'] for a in html('.b_algo')('h2')('a')]


def _extract_links_from_google(html):
    return [a.attrib['href'] for a in html('.l')] or \
        [a.attrib['href'] for a in html('.r')('a')]


def _extract_links_from_duckduckgo(html):
    html.remove_namespaces()
    links_anchors = html.find('a.result__a')
    results = []
    for anchor in links_anchors:
        link = anchor.attrib['href']
        url_obj = urlparse(link)
        parsed_url = parse_qs(url_obj.query).get('uddg', '')
        if parsed_url:
            results.append(parsed_url[0])
    return results


def _extract_links(html, search_engine):
    if search_engine == 'bing':
        return _extract_links_from_bing(html)
    if search_engine == 'duckduckgo':
        return _extract_links_from_duckduckgo(html)
    return _extract_links_from_google(html)


def _get_search_url(search_engine):
    return SEARCH_URLS.get(search_engine, SEARCH_URLS['google'])


def _is_blocked(page):
    for indicator in BLOCK_INDICATORS:
        if page.find(indicator) != -1:
            return True

    return False


def _get_links(query):
    search_engine = os.getenv('HOWDOI_SEARCH_ENGINE', 'google')
    search_url = _get_search_url(search_engine)

    result = _get_result(search_url.format(URL, url_quote(query)))
    if _is_blocked(result):
        _print_err('Unable to find an answer because the search engine temporarily blocked the request. '
                   'Please wait a few minutes or select a different search engine.')
        raise BlockError("Temporary block by search engine")

    html = pq(result)
    return _extract_links(html, search_engine)


def get_link_at_pos(links, position):
    if not links:
        return False

    if len(links) >= position:
        link = links[position - 1]
    else:
        link = links[-1]
    return link


def _format_output(code, args):
    if not args['color']:
        return code
    lexer = None

    # try to find a lexer using the StackOverflow tags
    # or the query arguments
    for keyword in args['query'].split() + args['tags']:
        try:
            lexer = get_lexer_by_name(keyword)
            break
        except ClassNotFound:
            pass

    # no lexer found above, use the guesser
    if not lexer:
        try:
            lexer = guess_lexer(code)
        except ClassNotFound:
            return code

    return highlight(code,
                     lexer,
                     TerminalFormatter(bg='dark'))


def _is_question(link):
    for fragment in BLOCKED_QUESTION_FRAGMENTS:
        if fragment in link:
            return False
    return re.search(r'questions/\d+/', link)


def _get_questions(links):
    return [link for link in links if _is_question(link)]


def _get_answer(args, links):
    link = get_link_at_pos(links, args['pos'])
    if not link:
        return False

    cache_key = link
    page = cache.get(link)
    if not page:
        page = _get_result(link + '?answertab=votes')
        cache.set(cache_key, page)

    html = pq(page)

    first_answer = html('.answer').eq(0)

    instructions = first_answer.find('pre') or first_answer.find('code')
    args['tags'] = [t.text for t in html('.post-tag')]

    if not instructions and not args['all']:
        text = get_text(first_answer.find('.post-text').eq(0))
    elif args['all']:
        texts = []
        for html_tag in first_answer.items('.post-text > *'):
            current_text = get_text(html_tag)
            if current_text:
                if html_tag[0].tag in ['pre', 'code']:
                    texts.append(_format_output(current_text, args))
                else:
                    texts.append(current_text)
        text = '\n'.join(texts)
    else:
        text = _format_output(get_text(instructions.eq(0)), args)
    if text is None:
        text = NO_ANSWER_MSG
    text = text.strip()
    return text


def _get_links_with_cache(query):
    cache_key = query + "-links"
    res = cache.get(cache_key)
    if res:
        if res == CACHE_EMPTY_VAL:
            res = False
        return res

    links = _get_links(query)
    if not links:
        cache.set(cache_key, CACHE_EMPTY_VAL)

    question_links = _get_questions(links)
    cache.set(cache_key, question_links or CACHE_EMPTY_VAL)

    return question_links


def build_splitter(splitter_character='=', splitter_length=80):
    return '\n' + splitter_character * splitter_length + '\n\n'


def _get_answers(args):
    """
    @args: command-line arguments
    returns: array of answers and their respective metadata
             False if unable to get answers
    """

    question_links = _get_links_with_cache(args['query'])
    if not question_links:
        return False

    answers = []
    initial_position = args['pos']
    multiple_answers = (args['num_answers'] > 1 or args['all'])

    for answer_number in range(args['num_answers']):
        current_position = answer_number + initial_position
        args['pos'] = current_position
        link = get_link_at_pos(question_links, current_position)
        answer = _get_answer(args, question_links)
        if not answer:
            continue
        if not args['link'] and not args['json_output'] and multiple_answers:
            answer = ANSWER_HEADER.format(link, answer, STAR_HEADER)
        answer += '\n'
        answers.append({
            'answer': answer, 
            'link': link, 
            'position': current_position
        })

    return answers


def _clear_cache():
    global cache
    if not cache:
        cache = FileSystemCache(CACHE_DIR, CACHE_ENTRY_MAX, 0)

    return cache.clear()


def _is_help_query(query: str):
    return any([query.lower() == help_query for help_query in SUPPORTED_HELP_QUERIES])


def _format_answers(res, args):
    if "error" in res:
        return res["error"]

    if args["json_output"]:
        return json.dumps(res)

    formatted_answers = []
    
    for answer in res:
        next_ans = answer["answer"]
        if args["link"]:  # if we only want links
            next_ans = answer["link"]
        formatted_answers.append(next_ans)
    
    return build_splitter().join(formatted_answers)


def _get_help_instructions():
    instruction_splitter = build_splitter(' ', 60)
    query = 'print hello world in python'
    instructions = [
        'Here are a few popular howdoi commands ',
        '>>> howdoi {} (default query)',
        '>>> howdoi {} -a (read entire answer)',
        '>>> howdoi {} -n [number] (retrieve n number of answers)',
        '>>> howdoi {} -l (display only a link to where the answer is from',
        '>>> howdoi {} -c (Add colors to the output)',
        '>>> howdoi {} -e (Specify the search engine you want to use e.g google,bing)'
    ]

    instructions = map(lambda s: s.format(query), instructions)

    return instruction_splitter.join(instructions)


def _get_cache_key(args):
    return str(args) + __version__


def print_stash(sl = []):
    if len(sl) == 0:
        stash_list = ['\nSTASH LIST:']
        commands = keep_utils.read_commands()

        if commands is None or len(commands.items()) == 0:
            print('No commands found in stash. Add a command with "howdoi -save <query>".')
            return

        for cmd, fields in commands.items():
            stash_list.append(
                '\033[4m\033[1m$ ' + fields['alias'] + '\033[0m\n\n' 
                + fields['desc'] + '\n')
    else:
        stash_list = [(
            '\033[4m\033[1m$ [' + str(i+1) + '] ' + x['fields']['alias'] + '\033[0m\n\n' + x['fields']['desc'] + '\n') 
            for i, x in enumerate(sl)]
    print(build_splitter('#').join(stash_list))

def _get_stash_key(args):
    stash_args = {}
    ignore_keys = ['stash_save', 'stash_view', 'stash_remove', 'stash_empty', 'tags']
    for key in args:
        if not (key in ignore_keys):
            stash_args[key] = args[key]
    return str(stash_args)


def remove_stash_command(cmd_key, title):
    commands = keep_utils.read_commands()
    if commands is not None and cmd_key in commands:
        keep_utils.remove_command(cmd_key)
        print('\n\033[1m\033[92m"' + title + '" removed from stash.\033[0m' + '\n\ncommand key data --- ' + cmd_key + '\n')
    else:
        print('\n\033[1m\033[91m"' + title + '" not found in stash.\033[0m' + '\n\ncommand key data --- ' + cmd_key + '\n')
    return ''


def _parse_cmd(args, res):
    cmd_key = _get_stash_key(args)
    answer = _format_answers(res, args)
    title = ''.join(args['query'])
    if args['stash_save']:
        keep_utils.save_command(cmd_key, answer, title)
        print_stash()
        return ''
    if args['stash_remove']:
        remove_stash_command(cmd_key)
    return answer


def howdoi(raw_query):
    args = raw_query
    if type(raw_query) is str:  # you can pass either a raw or a parsed query
        parser = get_parser()
        args = vars(parser.parse_args(raw_query.split(' ')))

    args['query'] = ' '.join(args['query']).replace('?', '')
    cache_key = _get_cache_key(args)

    if _is_help_query(args['query']):
        return _get_help_instructions() + '\n'

    res = cache.get(cache_key)

    if res:
        return _parse_cmd(args, res)

    try:
        res = _get_answers(args)
        if not res:
            res = {"error": "Sorry, couldn\'t find any help with that topic\n"}
        cache.set(cache_key, res)
    except (ConnectionError, SSLError):
        return {"error": "Failed to establish network connection\n"}
    finally:
        return _parse_cmd(args, res)


def get_parser():
    parser = argparse.ArgumentParser(description='instant coding answers via the command line')
    parser.add_argument('query', metavar='QUERY', type=str, nargs='*', help='the question to answer')
    parser.add_argument('-p', '--pos', help='select answer in specified position (default: 1)', default=1, type=int)
    parser.add_argument('-a', '--all', help='display the full text of the answer', action='store_true')
    parser.add_argument('-l', '--link', help='display only the answer link', action='store_true')
    parser.add_argument('-c', '--color', help='enable colorized output', action='store_true')
    parser.add_argument('-n', '--num-answers', help='number of answers to return', default=1, type=int)
    parser.add_argument('-C', '--clear-cache', help='clear the cache',
                        action='store_true')
    parser.add_argument('-j', '--json-output', help='return answers in raw json format',
                        action='store_true')
    parser.add_argument('-save', '--stash-save', help='stash a howdoi query and answer',
                        action='store_true')
    parser.add_argument('-view', '--stash-view', help='view your stash of commands',
                        action='store_true')
    parser.add_argument('-remove', '--stash-remove', help='remove a stash command',
                        action='store_true'),
    parser.add_argument('-empty', '--stash-empty', help='clear your stash of commands',
                        action='store_true')
    parser.add_argument('-v', '--version', help='displays the current version of howdoi',
                        action='store_true')
    parser.add_argument('-e', '--engine', help='change search engine for this query only (google, bing, duckduckgo)',
                        dest='search_engine', nargs="?", default='google')
    return parser


def prompt_stash_remove(args, stash_list, view_stash = True):
    if view_stash:
        print_stash(stash_list)

    last_index = len(stash_list)
    prompt = "\033[1m > Select a stash command to remove [1-" + str(last_index) + "] (0 to cancel): \033[0m"
    user_input = input(prompt)
    try:
        user_input = int(user_input)
        if user_input == 0:
            return
        elif user_input < 1 or user_input > last_index:
            print("\n\033[91mInput index is invalid.\033[0m")
            prompt_stash_remove(args, stash_list, False)
        cmd = stash_list[user_input - 1]
        cmd_key = cmd['command']
        cmd_name = cmd['fields']['alias']
        remove_stash_command(cmd_key, cmd_name)
        return
    except ValueError:
        print("\n\033[91mInvalid input. Must specify index of command.\033[0m")
        prompt_stash_remove(args, stash_list, False)
        return

def command_line_runner():
    parser = get_parser()
    args = vars(parser.parse_args())

    if args['version']:
        _print_ok(__version__)
        return

    if args['clear_cache']:
        if _clear_cache():
            _print_ok('Cache cleared successfully')
        else:
            _print_err('Clearing cache failed')
        return

    if args['stash_view']:
        print_stash()
        return

    if args['stash_empty']:
        os.system('keep init')
        return

    if args['stash_remove'] and len(args['query']) == 0:
        commands = keep_utils.read_commands()
        if commands is None or len(commands.items()) == 0:
            print('No commands found in stash. Add a command with "howdoi -save <query>".')
            return
            
        stash_list = []
        for cmd, field in commands.items():
            stash_list.append({
                'command': cmd,
                'fields': field
            })
        prompt_stash_remove(args, stash_list)
        return

    if not args['query']:
        parser.print_help()
        return

    if os.getenv('HOWDOI_COLORIZE'):
        args['color'] = True

    if not args['search_engine'] in SUPPORTED_SEARCH_ENGINES:
        _print_err('Unsupported engine.\nThe supported engines are: %s' % ', '.join(SUPPORTED_SEARCH_ENGINES))
        return
    elif args['search_engine'] != 'google':
        os.environ['HOWDOI_SEARCH_ENGINE'] = args['search_engine']

    utf8_result = howdoi(args).encode('utf-8', 'ignore')
    if sys.version < '3':
        print(utf8_result)
    else:
        # Write UTF-8 to stdout: https://stackoverflow.com/a/3603160
        sys.stdout.buffer.write(utf8_result)
    # close the session to release connection
    howdoi_session.close()


if __name__ == '__main__':
    command_line_runner()
