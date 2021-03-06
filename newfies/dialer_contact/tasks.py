#
# Newfies-Dialer License
# http://www.newfies-dialer.org
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright (C) 2011-2013 Star2Billing S.L.
#
# The Initial Developer of the Original Code is
# Arezqui Belaid <info@star2billing.com>
#

from django.conf import settings
from celery.decorators import task
from celery.task import Task
from celery.utils.log import get_task_logger
from dialer_campaign.models import Campaign, Subscriber
from common.only_one_task import only_one

logger = get_task_logger(__name__)


class ImportPhonebook(Task):
    """
    ImportPhonebook class call the import for a specific campaign_id and phonebook_id
    """
    @only_one(ikey="import_phonebook", timeout=60 * 5)
    def run(self, campaign_id, phonebook_id):
        """
        Read all the contact from phonebook_id and insert into subscriber
        """
        logger = self.get_logger()
        logger.info("TASK :: import_phonebook")
        obj_campaign = Campaign.objects.get(id=campaign_id)

        #Faster method, ask the Database to do the job
        importcontact_custom_sql(campaign_id, phonebook_id)

        #Count contact imported
        count_contact = Subscriber.objects.filter(campaign=campaign_id).count()

        #Add the phonebook id to the imported list
        if obj_campaign.imported_phonebook == '':
            sep = ''
        else:
            sep = ','
        obj_campaign.imported_phonebook = obj_campaign.imported_phonebook + \
            '%s%d' % (sep, phonebook_id)
        obj_campaign.totalcontact = count_contact
        obj_campaign.save()
        return True


@task()
def collect_subscriber(campaign_id):
    """
    This task will collect all the contact and create the Subscriber
    if the phonebook_id is no in the list of imported_phonebook IDs.

    **Attributes**:

        * ``campaign_id`` - Campaign ID
    """
    logger.debug("Collect subscribers for the campaign = %s" % str(campaign_id))

    #Retrieve the list of active contact
    obj_campaign = Campaign.objects.get(id=campaign_id)
    list_phonebook = obj_campaign.phonebook.all()

    for item_phonebook in list_phonebook:
        phonebook_id = item_phonebook.id

        # check if phonebook_id is missing in imported_phonebook list
        if not str(phonebook_id) in obj_campaign.imported_phonebook.split(','):
            #Run import
            logger.info("ImportPhonebook %d for campaign = %d" % (phonebook_id, campaign_id))
            keytask = 'import_phonebook-%d-%d' % (campaign_id, phonebook_id)
            ImportPhonebook().delay(obj_campaign.id, phonebook_id, keytask=keytask)

    return True


def importcontact_custom_sql(campaign_id, phonebook_id):
    # Call PL-SQL stored procedure
    #Subscriber.importcontact_pl_sql(campaign_id, phonebook_id)

    from django.db import connection, transaction
    cursor = connection.cursor()

    if settings.DATABASES['default']['ENGINE'] == 'django.db.backends.mysql':
        # Data insert operation - commit required
        sqlimport = "INSERT IGNORE INTO dialer_subscriber (contact_id, "\
            "campaign_id, duplicate_contact, status, created_date, updated_date) "\
            "SELECT id, %d, contact, 1, NOW(), NOW() FROM dialer_contact "\
            "WHERE phonebook_id=%d AND dialer_contact.status=1" % \
            (campaign_id, phonebook_id)

    elif settings.DATABASES['default']['ENGINE'] == 'django.db.backends.postgresql_psycopg2':
        # Data insert operation - http://stackoverflow.com/questions/12451053/django-bulk-create-with-ignore-rows-that-cause-integrityerror
        sqlimport = "LOCK TABLE dialer_subscriber IN EXCLUSIVE MODE;" \
            "INSERT INTO dialer_subscriber (contact_id, "\
            "campaign_id, duplicate_contact, status, created_date, updated_date) "\
            "SELECT id, %d, contact, 1, NOW(), NOW() FROM dialer_contact "\
            "WHERE phonebook_id=%d AND dialer_contact.status=1 AND NOT EXISTS (" \
            "SELECT 1 FROM dialer_subscriber WHERE "\
            "dialer_subscriber.campaign_id=%d "\
            "AND dialer_contact.id = dialer_subscriber.contact_id );" % \
            (campaign_id, phonebook_id, campaign_id)
    else:
        # Other DB
        logger.error("Database not supported (%s)" %
                     settings.DATABASES['default']['ENGINE'])
        return False

    cursor.execute(sqlimport)
    transaction.commit_unless_managed()
    return True
