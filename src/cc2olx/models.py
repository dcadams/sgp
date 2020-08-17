import os.path
import re
from textwrap import dedent
import zipfile

from cc2olx import filesystem
from cc2olx.settings import MANIFEST

DIFFUSE_SHALLOW_SECTIONS = False
DIFFUSE_SHALLOW_SUBSECTIONS = True

OLX_DIRECTORIES = [
    'about',
    'assets',
    'chapter',
    'course',
    'html',
    'info',
    'policies',
    'problem',
    'sequential',
    'static',
    'vertical',
]


def is_leaf(container):
    return 'identifierref' in container


def has_only_leaves(container):
    return all(is_leaf(child) for child in container.get('children', []))


class ResourceFile:
    def __init__(self, href):
        self.href = href

    def __repr__(self):
        return "<ResourceFile href={href} />".format(
            href=self.href,
        )


class ResourceDependency:
    def __init__(self, identifierref):
        self.identifierref = identifierref

    def __repr__(self):
        return "<ResourceDependency identifierref={identifierref} />".format(
            identifierref=self.identifierref,
        )


class Cartridge:
    def __init__(self, cartridge_file, workspace):
        self.cartridge = zipfile.ZipFile(str(cartridge_file))
        self.metadata = None
        self.resources = None
        self.resources_by_id = {}
        self.organizations = None
        self.normalized = None
        self.version = '1.1'
        self.file_path = cartridge_file
        self.directory = None
        self.ns = {}

        self.workspace = workspace

    def __repr__(self):
        filename = os.path.basename(self.file_path)
        text = '<{_class} version="{version}" file="{filename}" />'.format(
            _class=type(self).__name__,
            version=self.version,
            filename=filename,
        )
        return text

    def normalize(self):
        organizations = self.organizations
        count_organizations = len(organizations)
        if count_organizations == 0:
            # This is allowed by the spec.
            # But what do we do?
            # Should we build course content out of resources?
            # -> Mock a flattened section/subsection hierarchy?
            # -> Create a custom course tab(s)?
            # -> Dump links on the about page?
            organization = None
        elif count_organizations == 1:
            # This is allowed by the spec.
            organization = organizations[0]
        else:
            # This is _NOT_ allowed by the spec.
            # We could fall over, but let's just take the first...
            organization = organizations[0]
        if not organization:
            return
        identifier = organization.get('identifier', 'org_1')
        # An organization may have 0 or 1 item.
        # We'll treat it as the courseware root.
        course_root = organization.get('children', [])
        # Question: Does it have it have identifier="LearningModules"?
        count_root = len(course_root)
        if count_root == 0:
            # What to do?
            course_root = None
        elif count_root == 1:
            course_root = course_root[0]
        else:
            # What to do?
            # For now, just take the first.
            course_root = course_root[0]
        if not course_root:
            return
        sections = course_root.get('children', [])
        normal_course = {
            'children': [],
            'identifier': identifier,
            'structure': 'rooted-hierarchy',
        }
        for section in sections:
            if is_leaf(section):
                # Structure is too shallow.
                # Found leaf at section level.
                subsections = [
                    section,
                ]
            elif has_only_leaves(section):
                # Structure is too shallow.
                # Found only leaves inside section.
                if DIFFUSE_SHALLOW_SECTIONS:
                    subsections = [
                        {
                            'identifier': 'x'*34,
                            'title': 'none',
                            'children': [
                                subsection,
                            ],
                        }
                        for subsection in section.get('children', [])
                    ]
                else:
                    subsections = [
                        {
                            'identifier': 'x'*34,
                            'title': 'none',
                            'children': section.get('children', []),
                        },
                    ]
            else:
                subsections = section.get('children', [])
            normal_section = {
                'children': [],
                'identifier': section.get('identifier'),
                'identifierref': section.get('identifierref'),
                'title': section.get('title'),
            }
            if len(subsections) == 1:
                subsect = subsections[0]
                if subsect.get("title", "none") == "none":
                    subsect["title"] = section.get("title", "none")
            for subsection in subsections:
                if is_leaf(subsection):
                    # Structure is too shallow.
                    # Found leaf at subsection level.
                    units = [
                        subsection,
                    ]
                elif has_only_leaves(subsection):
                    # Structure is too shallow.
                    # Found only leaves inside subsection.
                    if DIFFUSE_SHALLOW_SUBSECTIONS:
                        units = [
                            {
                                'identifier': 'x'*34,
                                'title': unit.get('title', 'none'),
                                'children': [
                                    unit,
                                ],
                            }
                            for unit in subsection.get('children', [])
                        ]
                    else:
                        units = [
                            {
                                'identifier': 'x'*34,
                                'title': 'none',
                                'children': subsection.get('children', []),
                            },
                        ]
                else:
                    units = subsection.get('children', [])
                normal_subsection = {
                    'children': [],
                    'identifier': subsection.get('identifier'),
                    'identifierref': subsection.get('identifierref'),
                    'title': subsection.get('title'),
                }
                for unit in units:
                    if is_leaf(unit):
                        # Structure is too shallow.
                        # Found leaf at unit level.
                        components = [
                            unit,
                        ]
                    else:
                        components = unit.get('children', [])
                        components = self.flatten(components)
                    normal_unit = {
                        'children': [],
                        'identifier': unit.get('identifier'),
                        'identifierref': unit.get('identifierref'),
                        'title': unit.get('title'),
                    }
                    for component in components:
                        normal_unit['children'].append(component)
                    normal_subsection['children'].append(normal_unit)
                normal_section['children'].append(normal_subsection)
            normal_course['children'].append(normal_section)
        self.normalized = normal_course
        return normal_course

    def flatten(self, container):
        if is_leaf(container):
            return container
        if isinstance(container, list):
            children = container
        else:
            # Structure is too deep.
            # Flatten into current unit?
            # Found non-leaf at component level
            children = container.get('children', [])
        output = []
        for child in children:
            if is_leaf(child):
                output.append(child)
            else:
                leaves = self.flatten(child)
                output.extend(leaves)
        return output

    def get_resource_content(self, identifier):
        """
        Get the resource named by `identifier`.

        If the resource can be retrieved, returns a tuple: the first element
        indicates the type of content, either "html" or "link".  The second
        element is a dict with details, which vary by the type.

        If the resource can't be retrieved, returns a tuple of None, None.

        """
        res = self.resources_by_id.get(identifier)
        if res is None:
            print("*** Missing resource: {}".format(identifier))
            return None, None

        res_type = res["type"]
        if res_type == "webcontent":
            res_filename = self._res_filename(res["children"][0].href)
            if res_filename.suffix == ".html":
                try:
                    with res_filename.open() as res_file:
                        html = res_file.read()
                        if '%24IMS-CC-FILEBASE%24' in html:
                            html = html.replace('%24IMS-CC-FILEBASE%24', '/static')
                            html = html.replace('%20', '_')
                except Exception:
                    print("Failure reading {!r} from id {}".format(res_filename, identifier))
                    raise
                return "html", {"html": html}
            elif 'web_resources' in str(res_filename):
                if res_filename.suffix in ('.jpg', '.jpeg', '.png', '.apng', '.gif', '.ico', '.bmp', '.svg', '.webp'):
                    try:
                        static_filename = str(res_filename).split('web_resources/')[1].replace(' ', '_')
                        html = '<html>\n<head>\n<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>\n</head>\n<body>\n<p>\
                                <img src="{}" alt="{}">\
                                </p>\n</body>\n</html>'.format('/static/'+static_filename, static_filename)
                    except Exception:
                        return None, None
                    return "html", {"html": html}
                elif res_filename.suffix == ".pdf":
                    try:
                        static_filename = str(res_filename).split('web_resources/')[1].replace(' ', '_')
                        html = '<html>\n<head>\n<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>\n</head>\n<body>\n<p>\
                             <embed src="/static/{}" width="800px" height="650px" />\
                             </p>\n</body>\n</html>'.format(static_filename)
                    except Exception:
                        return None, None
                    return "html", {"html": html}
                else:
                    return None, None
            else:
                print("*** Skipping webcontent: {}".format(str(res_filename)))
                return None, None
        elif res_type == "imswl_xmlv1p1":
            tree = filesystem.get_xml_tree(self._res_filename(res["children"][0].href))
            root = tree.getroot()
            ns = {"wl": "http://www.imsglobal.org/xsd/imsccv1p1/imswl_v1p1"}
            title = root.find("wl:title", ns).text
            url = root.find("wl:url", ns).get("href")
            return "link", {"href": url, "text": title}
        elif res_type == "imsdt_xmlv1p1":
            tree = filesystem.get_xml_tree(self._res_filename(res["children"][0].href))
            if self._res_filename(res["children"][0].href).name.endswith('.xml'):
                root = tree.getroot()
                nswl = {"wl": "http://www.imsglobal.org/xsd/imsccv1p1/imswl_v1p1"}
                nsdt = {"wl": "http://www.imsglobal.org/xsd/imsccv1p1/imsdt_v1p1"}
                if root.find("wl:url", nswl) is not None:
                    title = root.find("wl:title", nswl).text
                    url = root.find("wl:url", nswl).get("href")
                    return "link", {"href": url, "text": title}
                else:
                    title = root.find("wl:title", nsdt).text
                    html = root.find("wl:text", nsdt).text
                    if not html:
                        return "discussion", {"html": None, "title": title}
                    elif '%24IMS-CC-FILEBASE%24' in html:
                        html = html.replace('%24IMS-CC-FILEBASE%24', '/static')
                        html = html.replace('%20', '_')
                    return "discussion", {"html": html, "title": title}
            else:
                return unimported_content_html(res_type, res)
        elif res_type == 'associatedcontent/imscc_xmlv1p1/learning-application-resource':
            _converted = False
            for index, child in enumerate(res.get('children')):
                if "assignment_settings.xml" in child.href:
                    try:
                        tree = filesystem.get_xml_tree(self._res_filename(res["children"][index].href))
                        root = tree.getroot()
                        ns = {"wl": "http://canvas.instructure.com/xsd/cccv1p0"}
                        title = root.find("wl:title", ns)
                        new_identifier = root.find("wl:external_tool_identifierref", ns)
                        external_tool_url = root.find("wl:external_tool_url", ns)
                        res = self.resources_by_id.get(new_identifier.text)
                        if res['type'] == "imsbasiclti_xmlv1p0":
                            data = self._parse_lti(res, external_tool_url if external_tool_url is not None else None)
                            _converted = True
                            return 'lti', data
                    except Exception:
                        return unimported_content_html(res_type, res)
            if not _converted:
                return unimported_content_html(res_type, res)

        elif res_type == 'imsbasiclti_xmlv1p0':
            data = self._parse_lti(res)
            return 'lti', data
        elif res_type == "imsqti_xmlv1p2/imscc_xmlv1p1/assessment":
            try:
                problems = []
                # tree = filesystem.get_xml_tree(self._res_filename(res["children"][0].href))
                # Updated
                try:
                    qti_id = res["children"][1].identifierref
                    new_res = self.resources_by_id.get(qti_id)
                    tree = filesystem.get_xml_tree(self._res_filename(new_res["children"][1].href))
                except Exception:
                    tree = filesystem.get_xml_tree(self._res_filename(res["children"][0].href))
                # End updated
                root = tree.getroot()
                ns = {"wl": "http://www.imsglobal.org/xsd/ims_qtiasiv1p2"}
                questions = root.findall("wl:assessment/wl:section/wl:item", ns)
                for que in questions:
                    que_type = que.find("wl:itemmetadata/wl:qtimetadata/wl:qtimetadatafield/wl:fieldentry", ns).text
                    if que_type in [
                        "true_false_question",
                        "multiple_choice_question",
                        "multiple_answers_question",
                        "cc.true_false.v0p1",
                        "cc.multiple_choice.v0p1",
                        "cc.multiple_response.v0p1"
                    ]:
                        que_title = que.get("title")
                        que_details = que.find("wl:presentation", ns)
                        que_text = str(que_details.find("wl:material/wl:mattext", ns).text)
                        # clean = re.compile('<.*?>')
                        # que_text = re.sub(clean, '', que_text)
                        que_options = que_details.findall("wl:response_lid/wl:render_choice/wl:response_label", ns)
                        if que_type in [
                            "true_false_question",
                            "multiple_choice_question",
                            "cc.true_false.v0p1",
                            "cc.multiple_choice.v0p1"
                        ]:
                            _type = "multiplechoiceresponse"
                            ans_id = [
                                que.find(
                                        "wl:resprocessing/wl:respcondition/wl:conditionvar/wl:varequal",
                                        ns
                                    ).text
                            ]
                        if que_type in ["multiple_answers_question", "cc.multiple_response.v0p1"]:
                            ans_list = que.findall(
                                            "wl:resprocessing/wl:respcondition/wl:conditionvar/wl:and/wl:varequal",
                                            ns
                                        )
                            _type = "multiple_response"
                            ans_id = [ans.text for ans in ans_list]

                        options = []
                        for option in que_options:
                            options.append(
                                {
                                    "is_correct": option.get("ident") in ans_id,
                                    "option_text": option.find("wl:material/wl:mattext", ns).text
                                }
                            )
                        context = {
                            "title": que_title,
                            "que_type": _type,
                            "que_text": que_text,
                            "options": options
                        }
                        problems.append(context)
                    elif que_type in [
                                        "short_answer_question",
                                        "cc.fib.v0p1",
                                        "numerical_question",
                                        "fill_in_multiple_blanks_question"
                                    ]:
                        que_title = que.get("title")
                        que_details = que.find("wl:presentation", ns)
                        que_text = str(que_details.find("wl:material/wl:mattext", ns).text)
                        # clean = re.compile('<.*?>')
                        # que_text = re.sub(clean, '', que_text)
                        que_text = re.sub(r"[\(\[].*?[\)\]]", "________ ", que_text)

                        if que_type in ["fill_in_multiple_blanks_question"]:
                            _type = "multiple_text_input"
                            ans_tags = que_details.findall("wl:response_lid", ns)
                            ans_list = []
                            for tag in ans_tags:
                                ans_opts = tag.findall("wl:render_choice/wl:response_label/wl:material/wl:mattext", ns)
                                ans_list.append([ans.text for ans in ans_opts])
                        if que_type in ["short_answer_question", "cc.fib.v0p1"]:
                            ans_list = que.findall("wl:resprocessing/wl:respcondition/wl:conditionvar/wl:varequal", ns)
                            _type = "text_input"
                            ans_list = [ans.text for ans in ans_list]
                        if que_type in ["numerical_question"]:
                            ans_list = que.findall(
                                            "wl:resprocessing/wl:respcondition/wl:conditionvar/wl:or/wl:varequal",
                                            ns
                                        )
                            _type = "numeric_input"
                            ans_list = [ans.text for ans in ans_list]

                        context = {
                            "title": que_title,
                            "que_type": _type,
                            "que_text": que_text,
                            "options": ans_list
                        }
                        problems.append(context)
                    elif que_type in ["multiple_dropdowns_question"]:
                        que_title = que.get("title")
                        que_details = que.find("wl:presentation", ns)
                        que_text = str(que_details.find("wl:material/wl:mattext", ns).text)
                        # clean = re.compile('<.*?>')
                        # que_text = re.sub(clean, '', que_text).replace('\n', "")
                        que_text = re.sub(r"[\(\[].*?[\)\]]", "________ ", que_text)
                        # que_options = que_details.findall("wl:response_lid/wl:render_choice/wl:response_label", ns)
                        que_options = que_details.findall("wl:response_lid/wl:render_choice", ns)
                        ans_ids = que.findall("wl:resprocessing/wl:respcondition/wl:conditionvar/wl:varequal", ns)
                        options_list = []
                        for _index, que_opt in enumerate(que_options):
                            ans_opts = que_opt.findall("wl:response_label", ns)
                            options = []
                            for option in ans_opts:
                                options.append(
                                    {
                                        "is_correct": option.get("ident") in ans_ids[_index].text,
                                        "option_text": option.find("wl:material/wl:mattext", ns).text
                                    }
                                )
                            options_list.append(options)
                        _type = "dropdowns_question"
                        context = {
                            "title": que_title,
                            "que_type": _type,
                            "que_text": que_text,
                            "options": options_list
                        }
                        problems.append(context)
                    elif que_type in ["matching_question"]:
                        que_title = que.get("title")
                        que_details = que.find("wl:presentation", ns)
                        que_text = str(que_details.find("wl:material/wl:mattext", ns).text)
                        # clean = re.compile('<.*?>')
                        # que_text = re.sub(clean, '', que_text).replace('\n', "")
                        que_text = re.sub(r"[\(\[].*?[\)\]]", "________ ", que_text)
                        # ans_tags = que_details.findall("wl:response_lid", ns)

                        que_options = que_details.findall("wl:response_lid", ns)
                        ans_ids = que.findall("wl:resprocessing/wl:respcondition/wl:conditionvar/wl:varequal", ns)
                        options_list = {}
                        for _index, que_opt in enumerate(que_options):
                            ans_opts = que_opt.findall("wl:render_choice/wl:response_label", ns)
                            options = []
                            for option in ans_opts:
                                options.append(
                                    {
                                        "is_correct": option.get("ident") in ans_ids[_index].text,
                                        "option_text": option.find("wl:material/wl:mattext", ns).text
                                    }
                                )
                            options_list[que_opt.find("wl:material/wl:mattext", ns).text] = options
                        _type = "matching_question"
                        context = {
                            "title": que_title,
                            "que_type": _type,
                            "que_text": que_text,
                            "options": options_list
                        }
                        problems.append(context)
                    elif que_type in ["text_only_question"]:
                        que_title = que.get("title")
                        que_details = que.find("wl:presentation", ns)
                        html_txt = str(que_details.find("wl:material/wl:mattext", ns).text)
                        if '%24IMS-CC-FILEBASE%24' in html_txt:
                            html_txt = html_txt.replace('%24IMS-CC-FILEBASE%24', '/static')
                            html_txt = html_txt.replace('%20', '_')
                        context = {
                            "title": que_title,
                            "que_type": que_type,
                            "que_text": html_txt
                        }

                        problems.append(context)
                    else:
                        print("*** Skipping problem: res_type: {} problem_type: {}".format(res_type, que_type))
                        # unimported_content_html(res_type, res)
                return "problems", {"problems": problems}
                # return "html", { "html": "problems" }
            except Exception:
                print("*** Skipping problem: res_type:{} problem_type{}".format(res_type, que_type))
                unimported_content_html(res_type, res)
        else:
            return unimported_content_html(res_type, res)

    def load_manifest_extracted(self):
        manifest = self._extract()
        tree = filesystem.get_xml_tree(manifest)
        root = tree.getroot()
        self._update_namespaces(root)
        data = self._parse_manifest(root)
        self.metadata = data['metadata']
        self.organizations = data['organizations']
        self.resources = data['resources']
        self.resources_by_id = {r['identifier']: r for r in self.resources}
        self.version = self.metadata.get('schema', {}).get('version', self.version)
        return data

    def write_xml(self, text, output_base, output_file):
        text += '\n'
        output_path = os.path.join(output_base, output_file)
        with open(output_path, 'w') as output:
            output.write(text)

    def get_course_xml(self):
        text = '<course org="{org}" course="{number}" url_name="{run}" />'.format(
            org=self.get_course_org(),
            number=self.get_course_number(),
            run=self.get_course_run(),
        )
        return text

    def get_run_xml(self):
        text = dedent("""\
            <course
                display_name="{title}"
                language="{language}"
            >
            </course>\
        """).format(
            title=self.get_title(),
            language=self.get_language(),
        ).strip()
        return text

    def get_title(self):
        # TODO: Choose a better default course title
        title = self.metadata.get('lom', {}).get('general', {}).get('title') or 'Default Course Title'
        return title

    def get_start_date(self):
        try:
            for resource in self.resources:
                for res_file in resource.get('children'):
                    if "course_settings.xml" in res_file.href:
                        res_tree = filesystem.get_xml_tree(self._res_filename(res_file.href))
                        root = res_tree.getroot()
                        ns = {"wl": "http://canvas.instructure.com/xsd/cccv1p0"}
                        return root.find("wl:start_at", ns).text
        except Exception:
            return None

    def get_end_date(self):
        try:
            for resource in self.resources:
                for res_file in resource.get('children'):
                    if "course_settings.xml" in res_file.href:
                        res_tree = filesystem.get_xml_tree(self._res_filename(res_file.href))
                        root = res_tree.getroot()
                        ns = {"wl": "http://canvas.instructure.com/xsd/cccv1p0"}
                        return root.find("wl:conclude_at", ns).text
        except Exception:
            return None

    def get_course_image(self):
        try:
            course_image_path = self._res_filename("web_resources/course_image")
            if os.path.exists(course_image_path):
                course_image_file = os.listdir(course_image_path)[0]
                return "course_image_" + course_image_file
            else:
                return None
        except Exception:
            return None

    def get_language(self):
        # TODO: ensure the type of language code in the metadata
        title = self.metadata.get('lom', {}).get('general', {}).get('language') or 'en'
        return title

    def get_course_org(self):
        # TODO: find a better value for this
        return 'org'

    def get_course_number(self):
        # TODO: find a better value for this
        return 'number'

    def get_course_run(self):
        # TODO: find a better value for this; lifecycle.contribute_date?
        return 'run'

    def _extract(self):
        path_extracted = filesystem.unzip_directory(self.file_path, self.workspace)
        self.directory = path_extracted
        manifest = path_extracted / MANIFEST
        return manifest

    def _update_namespaces(self, root):
        ns = re.match(r'\{(.*)\}', root.tag).group(1)
        version = re.match(r'.*/(imsccv\dp\d)/', ns).group(1)

        self.ns['ims'] = ns
        self.ns['lomimscc'] = "http://ltsc.ieee.org/xsd/{version}/LOM/manifest".format(
            version=version,
        )

    def _parse_manifest(self, node):
        data = dict()
        data['metadata'] = self._parse_metadata(node)
        data['organizations'] = self._parse_organizations(node)
        data['resources'] = self._parse_resources(node)
        return data

    def _parse_metadata(self, node):
        data = dict()
        metadata = node.find('./ims:metadata', self.ns)
        if metadata:
            data['schema'] = self._parse_schema(metadata)
            data['lom'] = self._parse_lom(metadata)
        return data

    def _parse_schema(self, node):
        schema_name = self._parse_schema_name(node)
        schema_version = self._parse_schema_version(node)
        data = {
            'name': schema_name,
            'version': schema_version,
        }
        return data

    def _parse_schema_name(self, node):
        schema_name = node.find('./ims:schema', self.ns)
        schema_name = schema_name.text
        return schema_name

    def _parse_schema_version(self, node):
        schema_version = node.find('./ims:schemaversion', self.ns)
        schema_version = schema_version.text
        return schema_version

    def _parse_lom(self, node):
        data = dict()
        lom = node.find('lomimscc:lom', self.ns)
        if lom:
            data['general'] = self._parse_general(lom)
            data['lifecycle'] = self._parse_lifecycle(lom)
            data['rights'] = self._parse_rights(lom)
        return data

    def _parse_general(self, node):
        data = {}
        general = node.find('lomimscc:general', self.ns)
        if general:
            data['title'] = self._parse_text(general, 'lomimscc:title/lomimscc:string')
            data['language'] = self._parse_text(general, 'lomimscc:language/lomimscc:string')
            data['description'] = self._parse_text(general, 'lomimscc:description/lomimscc:string')
            data['keywords'] = self._parse_keywords(general)
        return data

    def _parse_text(self, node, lookup):
        text = None
        element = node.find(lookup, self.ns)
        if element is not None:
            text = element.text
        return text

    def _parse_keywords(self, node):
        # TODO: keywords
        keywords = []
        return keywords

    def _parse_rights(self, node):
        data = dict()
        element = node.find('lomimscc:rights', self.ns)
        if element:
            data['is_restricted'] = self._parse_text(
                element,
                'lomimscc:copyrightAndOtherRestrictions/lomimscc:value',
            )
            data['description'] = self._parse_text(
                element,
                'lomimscc:description/lomimscc:string',
            )
            # TODO: cost
        return data

    def _parse_lifecycle(self, node):
        # TODO: role
        # TODO: entity
        data = dict()
        contribute_date = node.find(
            'lomimscc:lifeCycle/lomimscc:contribute/lomimscc:date/lomimscc:dateTime',
            self.ns
        )
        text = None
        if contribute_date is not None:
            text = contribute_date.text
        data['contribute_date'] = text
        return data

    def _parse_organizations(self, node):
        data = []
        element = node.find('ims:organizations', self.ns) or []
        data = [
            self._parse_organization(org_node)
            for org_node in element
        ]
        return data

    def _parse_organization(self, node):
        data = {}
        data['identifier'] = node.get('identifier')
        data['structure'] = node.get('structure')
        children = []
        for item_node in node:
            child = self._parse_item(item_node)
            if len(child):
                children.append(child)
        if len(children):
            data['children'] = children
        return data

    def _parse_item(self, node):
        data = {}
        identifier = node.get('identifier')
        if identifier:
            data['identifier'] = identifier
        identifierref = node.get('identifierref')
        if identifierref:
            data['identifierref'] = identifierref
        title = self._parse_text(node, 'ims:title')
        if title:
            data['title'] = title
        children = []
        for child in node:
            child_item = self._parse_item(child)
            if len(child_item):
                children.append(child_item)
        if children and len(children):
            data['children'] = children
        return data

    def _parse_resources(self, node):
        element = node.find('ims:resources', self.ns) or []
        data = [
            self._parse_resource(sub_element)
            for sub_element in element
        ]
        return data

    def _parse_resource(self, node):
        data = {}
        identifier = node.get('identifier')
        if identifier:
            data['identifier'] = identifier
        _type = node.get('type')
        if _type:
            data['type'] = _type
        href = node.get('href')
        if href:
            data['href'] = href
        intended_use = node.get('intended_use')
        if intended_use:
            data['intended_use'] = intended_use
        children = []
        for child in node:
            prefix, has_namespace, postfix = child.tag.partition('}')
            tag = postfix
            if tag == 'file':
                child_data = self._parse_file(child)
            elif tag == 'dependency':
                child_data = self._parse_dependency(child)
            elif tag == 'metadata':
                child_data = self._parse_resource_metadata(child)
            else:
                print('UNSUPPORTED RESOURCE TYPE', tag)
                continue
            if child_data:
                children.append(child_data)
        if children and len(children):
            data['children'] = children
        return data

    def _parse_file(self, node):
        href = node.get('href')
        resource = ResourceFile(href)
        return resource

    def _parse_dependency(self, node):
        identifierref = node.get('identifierref')
        resource = ResourceDependency(identifierref)
        return resource

    def _parse_resource_metadata(self, node):
        # TODO: this
        return None

    def _res_filename(self, file_name):
        return self.directory / file_name

    def _parse_lti(self, resource, launch_url=None):
        tree = filesystem.get_xml_tree(self._res_filename(resource["children"][0].href))
        root = tree.getroot()
        ns = {
            'blti': 'http://www.imsglobal.org/xsd/imsbasiclti_v1p0',
            'lticp': 'http://www.imsglobal.org/xsd/imslticp_v1p0',
            'lticm': 'http://www.imsglobal.org/xsd/imslticm_v1p0',
        }
        title = root.find('blti:title', ns).text
        description = root.find('blti:description', ns).text
        if launch_url is not None:
            launch_url = launch_url.text
        else:
            launch_url = root.find('blti:secure_launch_url', ns)
            if launch_url is None:
                launch_url = root.find('blti:launch_url', ns)
            if launch_url is not None:
                launch_url = launch_url.text
            else:
                launch_url = ''
        width = root.find("blti:extensions/lticm:property[@name='selection_width']", ns)
        if width is None:
            width = '500'
        else:
            width = width.text
        height = root.find("blti:extensions/lticm:property[@name='selection_height']", ns)
        if height is None:
            height = '500'
        else:
            height = height.text
        custom = root.find('blti:custom', ns)
        if custom is None:
            parameters = dict()
        else:
            parameters = {
                option.get('name'): option.text
                for option in custom
            }
        data = {
            'title': title,
            'description': description,
            'launch_url': launch_url,
            'height': height,
            'width': width,
            'custom_parameters': parameters,
        }
        return data


def unimported_content_html(res_type, res):
    text = "Unimported content: type = {!r}".format(res_type)
    if "href" in res:
        text += ", href = {!r}".format(res["href"])
    print("***", text)
    return "html", {"html": text}
