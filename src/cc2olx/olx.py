import io
import re
import os
import tarfile
import xml.dom.minidom
from shutil import copytree, rmtree


class OlxExport:
    def __init__(self, cartridge):
        self.cartridge = cartridge
        self.doc = None
        self.count = 0

    def xml(self):
        self.doc = xml.dom.minidom.Document()
        self.doc.appendChild(self.doc.createComment(" Generated by cc2olx "))
        xcourse = self.doc.createElement("course")
        self.doc.appendChild(xcourse)
        xcourse.setAttribute("org", self.cartridge.get_course_org())
        xcourse.setAttribute("course", "Some_cc_Course")
        xcourse.setAttribute("name", self.cartridge.get_title())
        start_date = self.cartridge.get_start_date()
        if start_date:
            xcourse.setAttribute("start", start_date)
        end_date = self.cartridge.get_end_date()
        if end_date:
            xcourse.setAttribute("end", end_date)
        xcourse.setAttribute("course_image", self.cartridge.get_course_image())

        tags = "chapter sequential vertical".split()
        self._add_olx_nodes(xcourse, self.cartridge.normalized['children'], tags)
        return self.doc.toprettyxml()

    def _add_olx_nodes(self, elt, data, tags):
        leaf = not tags
        all_child = []
        self.count = self.count + 1
        for dd in data:
            if leaf:
                type = None
                if "identifierref" in dd:
                    idref = dd["identifierref"]
                    type, details = self.cartridge.get_resource_content(idref, self.count)
                if type is None:
                    type = "html"
                    details = {
                        "html": "<p>MISSING CONTENT</p>",
                    }
                if type == "link":
                    type, details = convert_link_to_video(details)
                if type == "link":
                    type = "html"
                    details = {
                        "html": "<a href='{}'>{}</a>".format(details["href"], details.get("text", "")),
                    }
                if type == "html":
                    child = self.doc.createElement("html")
                    txt = self.doc.createCDATASection(details["html"])
                    child.appendChild(txt)
                elif type == "video":
                    child = self.doc.createElement("video")
                    child.setAttribute("youtube", "1.00:" + details["youtube"])
                    child.setAttribute("youtube_id_1_0", details["youtube"])
                elif type == 'lti':
                    child = self._create_lti_node(details)
                elif type == "problems":
                    if not details.get("problems"):
                        print("*** Skipping problems and/or problems are blank ")
                        continue
                    for que in details.get("problems"):
                        if que.get("que_type") in ["multiplechoiceresponse", "multiple_response"]:
                            all_child.append(self._create_multiplechoiceresponse(que))
                        elif que.get("que_type") in ["text_input", "numeric_input"]:
                            all_child.append(self._create_text_or_numeric_input(que))
                        elif que.get("que_type") in ["multiple_text_input"]:
                            all_child.append(self._create_multiple_text_input(que))
                        elif  que.get("que_type") in ["dropdowns_question"]:
                            all_child.append(self._create_dropdowns_question(que))
                        else:
                            print("*** Skipping problem: problem_type: {}".format(que.get("que_type")))
                else:
                    raise Exception("WUT")
            else:
                child = self.doc.createElement(tags[0])

            if not all_child:
                if "title" in dd:
                    child.setAttribute("display_name", dd["title"])
                elt.appendChild(child)
                if "children" in dd:
                    self._add_olx_nodes(child, dd["children"], tags[1:])
            else:
                for child in all_child:
                    if "title" in dd:
                        child.setAttribute("display_name", dd["title"])
                    elt.appendChild(child)
                    if "children" in dd:
                        self._add_olx_nodes(child, dd["children"], tags[1:])
            # if "title" in dd:
            #     child.setAttribute("display_name", dd["title"])
            # elt.appendChild(child)
            # if "children" in dd:
            #     self._add_olx_nodes(child, dd["children"], tags[1:])

    def _create_lti_node(self, details):
        node = self.doc.createElement('lti_consumer')
        custom_parameters = "[{params}]".format(
            params=', '.join([
                '"{key}={value}"'.format(
                    key=key,
                    value=value,
                )
                for key, value in details['custom_parameters'].items()
            ]),
        )
        node.setAttribute('custom_parameters', custom_parameters)
        node.setAttribute('description', details['description'])
        node.setAttribute('display_name', details['title'])
        node.setAttribute('inline_height', details['height'])
        node.setAttribute('inline_width', details['width'])
        node.setAttribute('launch_url', details['launch_url'])
        node.setAttribute('modal_height', details['height'])
        node.setAttribute('modal_width', details['width'])
        node.setAttribute('xblock-family', 'xblock.v1')
        return node

    def _create_multiplechoiceresponse(self, question):
        problem_node = self.doc.createElement('problem')
        problem_node.setAttribute('display_name', question.get('title'))
        if question.get("que_type") == "multiplechoiceresponse":
            _ch_node = self.doc.createElement('multiplechoiceresponse')
            opt_node = self.doc.createElement('choicegroup')
        elif question.get("que_type") == "multiple_response":
            _ch_node = self.doc.createElement('choiceresponse')
            opt_node = self.doc.createElement('checkboxgroup')
            
        que_text_node = self.doc.createElement('p')
        que_text = self.doc.createCDATASection(question.get('que_text'))
        for opt in question.get("options"):
            option_node = self.doc.createElement('choice')
            option_node.setAttribute("correct", str(opt.get("is_correct")).lower())
            opt_text = self.doc.createCDATASection(str(opt.get('option_text')))
            option_node.appendChild(opt_text)
            opt_node.appendChild(option_node)
            
        que_text_node.appendChild(que_text)

        _ch_node.appendChild(que_text_node)
        _ch_node.appendChild(opt_node)
        problem_node.appendChild(_ch_node)

        return problem_node


    def _create_text_or_numeric_input(self, question):
        problem_node = self.doc.createElement('problem')
        problem_node.setAttribute('display_name', question.get('title'))
        ans_option = question.get("options")
        if question.get('que_type') == "text_input":
            _qe_node = self.doc.createElement('stringresponse')
        elif question.get('que_type') == "numeric_input":
            _qe_node = self.doc.createElement('numericalresponse')
        if ans_option:
            _qe_node.setAttribute('answer', ans_option[0])
        
        que_text_node = self.doc.createElement('p')
        _qe_node.appendChild(que_text_node)
        
        que_text = self.doc.createCDATASection(question.get('que_text'))
        que_text_node.appendChild(que_text)
        
        for ans in ans_option[1:]:
            option_node = self.doc.createElement('additional_answer')
            option_node.setAttribute('answer', ans)
            _qe_node.appendChild(option_node)
        
        if question.get('que_type') == "text_input":
            text_line_node = self.doc.createElement('textline')
            text_line_node.setAttribute('size', '50')
            _qe_node.appendChild(text_line_node)
        if question.get('que_type') == "numeric_input":
            text_line_node = self.doc.createElement('formulaequationinput')
            _qe_node.appendChild(text_line_node)

        problem_node.appendChild(_qe_node)

        return problem_node

    def _create_multiple_text_input(self, question):
        problem_node = self.doc.createElement('problem')
        problem_node.setAttribute('display_name', question.get('title'))
        ans_option = question.get("options")

        for _index, ans_list in enumerate(ans_option):
            _qe_node = self.doc.createElement('stringresponse')
            if ans_list:
                _qe_node.setAttribute('answer', ans_list[0])

            if _index == 0:
                que_text_node = self.doc.createElement('p')
                _qe_node.appendChild(que_text_node)
                que_text = self.doc.createCDATASection(question.get('que_text'))
                que_text_node.appendChild(que_text)

            for ans in ans_list[1:]:
                option_node = self.doc.createElement('additional_answer')
                option_node.setAttribute('answer', ans)
                _qe_node.appendChild(option_node)

            text_line_node = self.doc.createElement('textline')
            text_line_node.setAttribute('size', '50')
            _qe_node.appendChild(text_line_node)
            problem_node.appendChild(_qe_node)

        return problem_node

    def _create_dropdowns_question(self, question):
        problem_node = self.doc.createElement('problem')
        problem_node.setAttribute('display_name', question.get('title'))
        _ch_node = self.doc.createElement('optionresponse')
        que_text_node = self.doc.createElement('p')
        que_text = self.doc.createCDATASection(question.get('que_text'))

        que_text_node.appendChild(que_text)
        _ch_node.appendChild(que_text_node)
        for opt_lists in question.get("options"):
            opt_node = self.doc.createElement('optioninput')
            for opt in opt_lists:
                option_node = self.doc.createElement('option')
                option_node.setAttribute("correct", str(opt.get("is_correct")).lower())
                opt_text = self.doc.createCDATASection(str(opt.get('option_text')))
                option_node.appendChild(opt_text)
                opt_node.appendChild(option_node)
            _ch_node.appendChild(opt_node)
        
        problem_node.appendChild(_ch_node)

        return problem_node
        
def convert_link_to_video(details):
    """Possibly convert a link to a video."""
    # YouTube links can be like this: https://www.youtube.com/watch?v=gQ-cZRmHfs4&amp;amp;list=PL5B350D511278A56B
    ytmatch = re.search(r"youtube.com/watch\?v=([-\w]+)", details["href"])
    if ytmatch:
        return "video", {"youtube": ytmatch.group(1)}
    return "link", details


def onefile_tar_gz(filetgz, content, string_name):
    tarinfo = tarfile.TarInfo(string_name)
    tarinfo.size = len(content)

    with tarfile.open(str(filetgz), 'w:gz') as tgz:
        tgz.addfile(tarinfo, io.BytesIO(content))

def multifile_tar_gz(filetgz, olx_file, cartridge_dir, asset_path, contents, string_name="course.xml"):
    """
    crates tar.gz file having assets(static dir) of course and course.xml
    """
    tarinfo = tarfile.TarInfo(string_name)
    tarinfo.size = len(contents)
    try:
        static_dir = cartridge_dir + 'assets/static'
        if os.path.exists(static_dir) and os.path.isdir(static_dir):
            rmtree(static_dir)
        copytree(asset_path, static_dir)
    except Exception as e:
        pass
    else:
        with tarfile.open(filetgz, 'w:gz') as tgz:
            tgz.addfile(tarinfo, io.BytesIO(contents))
            tgz.add(static_dir, arcname='static')
