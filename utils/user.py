import time
import config as c

from utils.logger import logger


class User:
    """
    this User object is used to contain all user-related methods
    instantiated during auth using the auth response payload

    the User class variable contains the src table for storing user data

    for Streamlit apps, this object is loaded to st.session_state
    and used as the entry point for:
        - invoking user-related methods, or
        - accessing user-specific variables
    """

    table = c.ddb.Table(c.USERS_TABLE)

    def __init__(self, payload):
        self.user_id = payload.get("sub")

        # the iat ("issued at") claim of the current identity token. Streamlit
        # freezes the OIDC claims in a signed cookie at login time and reuses
        # them across page refreshes, so iat is stable for the life of a login
        # and only changes when the user genuinely logs in again. We use it to
        # avoid treating page refreshes as fresh logins.
        current_login_iat = payload.get("iat")

        if self.is_new_user():
            self.init_user_data(payload)
            self.set_user_data()
            logger.info(f"{self} just registered as a new user!")
            c.po.send_notification(f"{self} just registered as a new user!")

        else:
            self.get_user_data()
            stored_login_iat = getattr(self, "login_token_iat", None)

            # a refresh reuses the same identity cookie => same iat. Skip the
            # login side effects (counter bump + notification) in that case.
            is_refresh = (
                current_login_iat is not None
                and stored_login_iat is not None
                and current_login_iat == stored_login_iat
            )

            if is_refresh:
                logger.info(f"{self} resumed an existing session (page refresh); not counted as a new login")
            else:
                self.record_login(current_login_iat)

                ordinal = "st" if self.num_logins == 1 else "nd" if self.num_logins == 2 else "rd" if self.num_logins == 3 else "th"
                logger.info(f"{self} just logged in for the {self.num_logins}{ordinal} time!")
                c.po.send_notification(f"{self} just logged in for the {self.num_logins}{ordinal} time!")

        self.load_user_variables()

    def __repr__(self):
        return f"User(name={self.name!r}, email={self.email!r})"  # !r ensures proper quoting/escaping.

    def is_new_user(self):
        response = self.table.get_item(Key={"user_id": self.user_id})

        # Item key exists if user_id in table
        return "Item" not in response

    def init_user_data(self, payload):
        """Extracts standard fields from ID token payload into instance variables."""

        # payload data
        self.name = payload.get("name")
        self.email = payload.get("email")
        self.first_name = payload.get("given_name")
        self.last_name = payload.get("family_name")
        self.picture_url = payload.get("picture")
        self.created_at = payload.get("iat", int(time.time()))

        # additional attributes
        self.num_logins = 1
        self.last_login = self.created_at

        # fingerprint of the login token; used to tell refreshes from new logins
        self.login_token_iat = self.created_at

    def set_user_data(self):
        """update user data in DynamoDB"""
        # __dict__ contains all instance variables
        item = {k: v for k, v in self.__dict__.items()}
        self.table.put_item(Item=item)

    def get_user_data(self):
        """get user data from DynamoDB"""
        response = self.table.get_item(Key={"user_id": self.user_id})
        item = response.get("Item", {})

        for k, v in item.items():
            setattr(self, k, v)

    def get_user_attribute(self, attr_name):
        """Fetch a single attribute from DynamoDB."""
        response = self.table.get_item(
            Key={"user_id": self.user_id},
            ProjectionExpression=attr_name
        )

        attr = response.get("Item", {}).get(attr_name)

        # attach to self
        # useful for repeat access of attr without using more DynamoDB read-capacity units
        setattr(self, attr_name, attr)

        return attr

    def record_login(self, login_token_iat=None):
        """
        Record a genuine login: update the timestamp, atomically increment the
        login counter, and store the identity token's iat so future page
        refreshes (which reuse the same iat) are not counted as new logins.
        """
        self.last_login = int(time.time())

        # keep the in-memory counter in step for the log/notification message
        self.num_logins = int(getattr(self, "num_logins", 0)) + 1

        # ADD keeps the DynamoDB increment atomic under concurrent logins
        update_expr = "SET last_login = :ts ADD num_logins :inc"
        expr_values = {":ts": self.last_login, ":inc": 1}

        if login_token_iat is not None:
            self.login_token_iat = login_token_iat
            update_expr = "SET last_login = :ts, login_token_iat = :iat ADD num_logins :inc"
            expr_values[":iat"] = login_token_iat

        self.table.update_item(
            Key={"user_id": self.user_id},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values
        )

    def increment_attribute(self, attr_name, increment=1):
        """
        Increment or create the stated attribute for user by the specified amount.
        Note: this does not support attributes with nested paths (e.g. 'a.b.c')
        """

        if not isinstance(increment, int) or increment < 1:
            raise ValueError("increment must be a positive integer")

        if not isinstance(attr_name, str) or not attr_name or "." in attr_name:
            raise ValueError("attr_name must be a non-empty top-level string")

        self.table.update_item(
            Key={"user_id": self.user_id},
            UpdateExpression="ADD #attr :inc",
            ExpressionAttributeNames={
                "#attr": attr_name,
            },
            ExpressionAttributeValues={
                ":inc": increment
            }
        )

    # ---- project specific logic ----

    def load_user_variables(self):
        """
        Defines per-user S3 paths. User data is nested under users/<email>/
        so each user gets their own isolated trades and ticker cache.
        """

        self.ROOT_FOLDER = f"users/{self.email}"
        self.TRADES_JSON_PATH = f"{self.ROOT_FOLDER}/{c.TRADES_JSON_FILENAME}"
        self.TICKER_DATA_PATH = f"{self.ROOT_FOLDER}/{c.TICKER_DATA_FILENAME}"
