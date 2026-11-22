"""Save the speech intervals WhisperX detects before it merges them for transcription."""

from whisperx.__main__ import cli
from whisperx.utils import WriteJSON
from whisperx.vads.pyannote import Binarize, Pyannote


def main():
    intervals = []
    merge_chunks = Pyannote.merge_chunks
    write_result = WriteJSON.write_result

    def capture_speech(segments, chunk_size, onset=0.5, offset=None):
        speech = Binarize(onset=onset, offset=offset)(segments)
        intervals.append([[max(0, span.start), span.end] for span in speech.get_timeline().support()
                          if span.end > 0])
        return merge_chunks(segments, chunk_size, onset=onset, offset=offset)

    def write_with_speech(self, result, file, options):
        if intervals:
            result = {**result, "speech_intervals": intervals.pop(0)}
        return write_result(self, result, file, options)

    Pyannote.merge_chunks = staticmethod(capture_speech)
    WriteJSON.write_result = write_with_speech
    cli()


if __name__ == "__main__":
    main()
