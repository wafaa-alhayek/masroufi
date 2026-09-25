"""The note cleaning pipeline: redact, translate, tidy.

Jev turns out to handle Arabic natively, so translation is no longer what makes
classification work. It earns its place for two other reasons, which is why it stays:

  * **Vendor identity.** Exact-alias and close-match comparisons work far better
    against one normalised form than against a mix of Arabic and transliterated
    spellings of the same shop.
  * **Two forms are better than one.** The raw note is what the household recognises;
    the cleaned note is what the model read. Keeping both is what makes a wrong
    category diagnosable rather than merely wrong.

Redaction is unconditional either way — it does not depend on any model's language
ability, and it is the step that keeps account numbers off the network.

Runs once per unique note, before anything is classified or stored, and produces
everything downstream needs:

    "POS سوبر ماركت الأمل 884213"
      raw          POS سوبر ماركت الأمل 884213
      redacted     POS سوبر ماركت الأمل [NUM]
      english      POS Al Amal Supermarket
      vendor_name  Al Amal Supermarket        <- what gets stored
      match_key    al amal supermarket        <- what vendors are matched on

Redaction comes first so neither the translation provider nor the decision model
sees an account number. Tidying comes last so it operates on English when
translation is on, and on the original when it is off.
"""

from dataclasses import dataclass

from app import tidy
from app.redact import normalise, redact
from app.singleflight import SingleFlight
from app.translate.base import Translator


@dataclass(frozen=True)
class CleanNote:
    raw: str
    redacted: str
    english: str
    vendor_name: str
    match_key: str
    translated: bool

    @property
    def is_filler_only(self) -> bool:
        """No vendor survived the tidying — `POS 3352119`, `MISC DEBIT 7781`.

        These are the notes that cannot be identified from text at all, and are
        instead handled by the recurring-amount detector and the user.
        """
        return not self.vendor_name

    @property
    def for_classifier(self) -> str:
        """What the decision model sees: the tidied name, or the redacted note
        when tidying left nothing, so the model still gets a chance at it."""
        return self.vendor_name or self.redacted


class NoteCleaner:
    def __init__(self, translator: Translator) -> None:
        self._translator = translator
        self._flight: SingleFlight[str, CleanNote] = SingleFlight()

    async def clean(self, raw: str) -> CleanNote:
        return await self._flight.do(normalise(raw), lambda: self._clean(raw))

    async def _clean(self, raw: str) -> CleanNote:
        redacted = redact(raw)
        english = await self._translator.to_english(redacted)
        return CleanNote(
            raw=raw,
            redacted=redacted,
            english=english,
            vendor_name=tidy.display_name(english),
            match_key=tidy.match_key(english),
            translated=english != redacted,
        )

    async def aclose(self) -> None:
        await self._translator.aclose()

    @property
    def size(self) -> int:
        return self._flight.size
