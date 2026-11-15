"""The doctor area's names (epic-doctor-profile entry 1): what the header and the home page
show of the signed-in user.

The names are encrypted in the database (accounts.crypto), so the initials and the display
name are worked out here, in Python, from the decrypted values; never in a query. Each
name is normalised to NFC first, so a name stored decomposed (e.g. from macOS: "E" and a
combining accent) gives its accented letter, not the bare base letter.
"""
import unicodedata


def _clean(value):
    return unicodedata.normalize('NFC', value or '').strip()


def initials(user):
    """The avatar's letters, upper case: the first letters of the first and last names,
    one of them when only one is set, else the email's first letter ('' without one)."""
    letters = ''.join(name[0] for name in (_clean(user.first_name), _clean(user.last_name))
                      if name)
    if not letters:
        letters = _clean(user.email)[:1]
    return letters.upper()


def first_name(user):
    """The greeting's name: the first name ('' when unset)."""
    return _clean(user.first_name)


def display_name(user):
    """The profile card's name: "{title} {first} {last}", the title in the page language;
    the parts that are unset are left out, and the email stands in when neither name is
    set."""
    names = [name for name in (_clean(user.first_name), _clean(user.last_name)) if name]
    if not names:
        return _clean(user.email)
    title = str(user.get_title_display()) if user.title else ''
    return ' '.join([title, *names] if title else names)


def country_name(user):
    """The country's name in the active language ('' when unset)."""
    return str(user.country.name) if user.country else ''


def profile_line(user):
    """The profile card's second line (epic-doctor-profile entry 2): "Profession · City",
    the profession in the active language, when both are set; otherwise the country's
    name, as before."""
    from .models import Profile

    profile = Profile.objects.filter(user_id=user.pk).first() if user.pk else None
    if profile is not None:
        city = _clean(profile.city)
        if profile.profession and city:
            return f'{profile.get_profession_display()} · {city}'
    return country_name(user)
