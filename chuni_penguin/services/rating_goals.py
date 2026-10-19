"""Original A rating goal formulas, independent of Discord and data sources."""

from decimal import Decimal
from math import ceil

from chuni_penguin.calculation import calculate_rating
from chuni_penguin.constants import MAX_DIFFICULTY
from chuni_penguin.types import RatingFrame, RatingType
from chuni_penguin.utils import floor_to_ndp


def rating_reach_content(
    rating_type: RatingType,
    target_rating: Decimal,
    current_rating: Decimal,  # should be 4dp for best results
    total_scores: int,
    frame: RatingFrame,
    each: Decimal | None,
    count: int | None,
):
    if each is None and (count is None or count == 1):
        # Reach goal with only one score
        raw_rating_required = (target_rating - current_rating) * total_scores
        frame_floor = (
            (frame.scores[-1].rating or Decimal(0))
            if len(frame.scores) == frame.num_scores
            else Decimal(0)
        )
        play_rating_required = ceil((frame_floor + raw_rating_required) * 100) / 100

        return (
            f"To reach {target_rating:.2f} {rating_type} with one score in your "
            f"{frame.type.name}{frame.num_scores}, you need to set a **{play_rating_required:.2f}** "
            f"rating play."
        ) + (
            " Good luck, I guess."
            if play_rating_required >= calculate_rating(1010000, MAX_DIFFICULTY)
            else ""
        )
    if (
        each is not None
        and len(frame.scores) == frame.num_scores
        and frame.scores[-1].rating is not None
        and each < frame.scores[-1].rating
    ):
        # Given rating is below best50 floor
        return (
            f"New {frame.type.name}{frame.num_scores} scores require "
            f"at least **{frame.scores[-1].rating:.2f}** rating, so {target_rating:.2f} {rating_type} "
            f"can't be reached with {each:.2f} rating scores."
        )
    if each is not None:
        # Given rating would be in best50
        num_scores = 0
        # Rating list from bottom to top, fill in unfilled slots with 0 rating
        ratings = [Decimal(0)] * (frame.num_scores - len(frame.scores)) + [
            frame.scores[-(i + 1)].rating or Decimal(0)
            for i in range(len(frame.scores))
        ]

        for rating in ratings:
            if rating >= each:
                break

            current_rating += (each - rating) / 50
            num_scores += 1

            if current_rating >= target_rating:
                break

        if current_rating >= target_rating:
            return (
                f"To reach {target_rating:.2f} {rating_type}, you need to set **{num_scores}** "
                f"score{'s' if num_scores > 1 else ''} of **{each:.2f}** rating in your "
                f"{frame.type.name}{frame.num_scores}."
            )

        return (
            f"Filling up your {frame.type.name}{frame.num_scores} with {each:.2f} rating "
            f"plays would only lead to {current_rating:.4f} {rating_type}, which is still less "
            f"than {target_rating:.2f} {rating_type}."
        )

    if count is not None:
        if count > frame.num_scores:
            return f"{count} scores is more than the number of scores in {frame.type.name}{frame.num_scores}."

        # count is not None and count > 1 (because first condition checks count == 1)
        # calculate play rating needed for each play to achieve the given rating
        # Ratings of bottom n plays.
        # - If there are more than `count` unfilled slots, take a list of `count` 0s.
        # - If there are less than `count` unfilled slots, take a list of 0s of unfilled
        # slots, and then take the rest from the given data.
        ratings = [Decimal(0)] * min(count, frame.num_scores - len(frame.scores)) + [
            frame.scores[-(i + 1)].rating or Decimal(0)
            for i in range(max(0, count - (frame.num_scores - len(frame.scores))))
        ]

        # Find the minimum rating to replace each entry in `ratings` with so that
        # it reaches the new target rating.
        raw_rating_required = (target_rating - current_rating) * total_scores
        required = (
            Decimal(
                ceil(
                    (sum(ratings, Decimal(0)) + raw_rating_required)
                    / len(ratings)
                    * 100
                )
            )
            / 100
        )
        count_less_than_required = sum(
            1
            for entry in frame.scores
            if entry.rating is None or entry.rating < required
        )

        return (
            (
                f"To reach {target_rating:.2f} {rating_type} with {count} score{'s' if count > 1 else ''} "
                f"of the same rating in {frame.type.name}{frame.num_scores}, each of them "
                f"would need to be **{required:.2f}** rating"
            )
            + (
                f", but you only have {count_less_than_required} scores less than {required:.2f} rating."
                if count_less_than_required < count
                else "."
            )
            + (
                " Good luck, I guess."
                if required >= calculate_rating(1010000, MAX_DIFFICULTY)
                else ""
            )
        )

    return "Uh oh, you hit a bug! Please annoy beerpsi."


def whatif_content(
    frame: RatingFrame,
    current_rating: Decimal,
    play_rating: Decimal,
    text_before_replacing: str = "",
):
    replacing: Decimal | None = None

    if len(frame.scores) < frame.num_scores:
        rating_increase = floor_to_ndp(play_rating / 50, 4)
    else:
        replacing = frame.scores[-1].rating or Decimal(0)
        rating_increase = max((Decimal(play_rating) - replacing) / 50, Decimal(0))

    return (
        f"**+{rating_increase:.4f}** ({current_rating:.4f} → {current_rating + rating_increase:.4f}){text_before_replacing}"
        + (
            f", replacing a {replacing:.2f} rating play"
            if replacing is not None and rating_increase > 0
            else ""
        )
    )
