
import re, os

class IdFinder:
    def __init__(self, rcHandler, pattern_key):
        idPatternStr = rcHandler.get(pattern_key, [ "general"], "")
        self.idPattern = None
        if idPatternStr:
            self.idPattern = re.compile(idPatternStr, re.DOTALL)
            
    def __bool__(self):
        return bool(self.idPattern)
            
    def extractIdFromText(self, text):
        idMatch = self.idPattern.match(text)
        if idMatch is not None:
            groups = idMatch.groups()
            if len(groups) > 0:
                for group in reversed(groups):
                    if group is not None:
                        return group
            else:
                return idMatch.group(0)

def read_alterations_line(fn):
    with open(fn) as f:
        for line in f:
            if line.startswith("alterations ="):
                return line.strip() + ","

ID_ALTERATIONS_RC_FILE = "id_alterations.rc"
def make_id_alterations_rc_file(id_mapping):
    fn = ID_ALTERATIONS_RC_FILE
    if os.path.isfile(fn):
        alterations_line = read_alterations_line(fn)
    else:
        alterations_line = "alterations = "
    with open(fn, "a") as f:
        f.write("[general]\n")
        f.write(alterations_line + ",".join(id_mapping) + "\n\n")
        for old_id, new_id in id_mapping.items():
            f.write("[" + old_id + "]\n")
            f.write('match_pattern = ' + old_id + '\n')
            f.write('replacement = ' + new_id + '\n')
    return fn    